from typing import get_args

from ..profiles import HarnessName, ProfileOptions


def fallback_profile(profile: ProfileOptions, harness: str) -> ProfileOptions:
    """Carry portable settings into a fresh native conversation."""
    return profile.model_copy(
        update={
            "harness": harness,
            "harness_options": {
                key: value
                for key, value in profile.harness_options.items()
                if key == "interrupt_on" or key in get_args(HarnessName)
            },
        }
    )
