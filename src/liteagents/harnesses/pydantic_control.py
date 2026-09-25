"""Record/replay and retry native Pydantic AI model requests without another loop."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models import CompletedStreamedResponse
from pydantic_ai.models.wrapper import WrapperModel

from ..runtime.control import CURRENT, retryable, stable
from ..storage.store import digest


def encode_response(response: Any) -> Any:
    return ModelMessagesTypeAdapter.dump_python([response], mode="json")


def decode_response(value: Any) -> Any:
    return ModelMessagesTypeAdapter.validate_python(value)[0]


class ControlledModel(WrapperModel):
    def __init__(self, models: list[tuple[str, Any, dict[str, Any]]]):
        super().__init__(models[0][1])
        self.models = models

    async def request(self, messages, model_settings, model_request_parameters):
        return await self._request(messages, model_settings, model_request_parameters)

    async def _request(
        self,
        messages,
        model_settings,
        model_request_parameters,
        *,
        streaming=False,
        run_context=None,
    ):
        control = CURRENT.get()
        if control is None:
            return await super().request(messages, model_settings, model_request_parameters)
        wire = stable(ModelMessagesTypeAdapter.dump_python(messages, mode="json"))
        for index, (name, model, settings) in enumerate(self.models):
            arguments = {"model": name, "messages": wire}

            async def invoke(model=model, settings=settings, name=name):
                options = {**(model_settings or {}), **settings}
                if streaming:
                    from pydantic_ai.messages import (
                        PartDeltaEvent,
                        PartStartEvent,
                        TextPart,
                        TextPartDelta,
                    )

                    async with model.request_stream(
                        messages, options, model_request_parameters, run_context
                    ) as stream:
                        async for event in stream:
                            text = None
                            if isinstance(event, PartStartEvent) and isinstance(
                                event.part, TextPart
                            ):
                                text = event.part.content
                            elif isinstance(event, PartDeltaEvent) and isinstance(
                                event.delta, TextPartDelta
                            ):
                                text = event.delta.content_delta
                            if text:
                                await control.emit("text_delta", text=text, model=name)
                        return stream.get()
                return await model.request(messages, options, model_request_parameters)

            try:
                return await control.call(
                    "model",
                    digest(arguments),
                    arguments,
                    invoke,
                    encode=encode_response,
                    decode=decode_response,
                )
            except Exception as exc:
                if index == len(self.models) - 1 or not retryable(exc):
                    raise
                await control.emit("model_fallback", previous=name, model=self.models[index + 1][0])
        raise AssertionError("unreachable")

    @asynccontextmanager
    async def request_stream(
        self, messages, model_settings, model_request_parameters, run_context=None
    ):
        control = CURRENT.get()
        # Drain the native stream while publishing live tokens. Commit the full
        # response before the native loop can dispatch its tools.
        if control and (control.store or control.profile.recovery):
            response = await self._request(
                messages,
                model_settings,
                model_request_parameters,
                streaming=True,
                run_context=run_context,
            )
            yield CompletedStreamedResponse(
                response, model_request_parameters=model_request_parameters, replay_events=True
            )
        else:
            async with super().request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as stream:
                yield stream
