class EngError(Exception):
    """A compile-time or run-time error, reported in plain English."""

    def __init__(self, message, line=None, file=None):
        super().__init__(message)
        self.message = message
        self.line = line
        self.file = file

    def report(self, default_file=""):
        import os
        file = os.path.basename(self.file or default_file)
        if self.line:
            return f"{file}, line {self.line}:\n{self.message}"
        return f"{file}:\n{self.message}" if file else self.message


class AmbiguityError(EngError):
    pass
