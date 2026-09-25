"""Public errors distinguish configuration, execution, and run identity failures."""


class LiteAgentsError(Exception):
    pass


class ConfigurationError(LiteAgentsError, ValueError):
    pass


class MissingDependencyError(ConfigurationError):
    pass


class UnsupportedFeatureError(ConfigurationError):
    pass


class RunAlreadyExistsError(LiteAgentsError):
    pass


class RunNotFoundError(LiteAgentsError):
    pass


class HarnessError(LiteAgentsError):
    pass
