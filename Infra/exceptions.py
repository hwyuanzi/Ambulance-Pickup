# Exception classes
class ValidationError(ValueError):
    pass


class FormatSyntaxError(ValidationError):
    pass


class DataMismatchError(ValidationError):
    pass


class IllegalPlanError(ValidationError):
    pass