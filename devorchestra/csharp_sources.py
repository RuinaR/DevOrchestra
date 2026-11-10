"""Admission grammar for two editable, side-effect-free C# arithmetic modules."""
import re

from .lease import Blocked

FILES = {"Scale.cs", "Calculation.cs"}
KIND = "mxc-dotnet-arithmetic-v1"
INSTRUCTION = ("Scale.cs must contain only 'static class Scale { public static int Apply(int value) "
               "=> checked(value * 2); }' and Calculation.cs only 'static class Calculation { "
               "public static int Twice(int value) => checked(Scale.Apply(value) + 0); }'. "
               "Change the integer constants or the operator. Scale allows +, - or *. "
               "Calculation allows + or -. Each constant must be an unsigned integer from 0 to 999 "
               "with no leading zero. Keep both files and all identifiers.")
PATTERNS = {
    "Scale.cs": r"static class Scale \{ public static int Apply\(int value\) => checked\(value [*+-] (?:0|[1-9][0-9]{0,2})\); \}",
    "Calculation.cs": r"static class Calculation \{ public static int Twice\(int value\) => checked\(Scale\.Apply\(value\) [+-] (?:0|[1-9][0-9]{0,2})\); \}",
}


def validate(sources):
    if set(sources) != FILES:
        raise Blocked("C# batch requires the two approved arithmetic modules")
    for name, source in sources.items():
        try:
            text = " ".join(source.decode("ascii").split())
        except UnicodeError:
            raise Blocked("C# source is outside the arithmetic grammar") from None
        if not re.fullmatch(PATTERNS[name], text):
            raise Blocked("C# source is outside the arithmetic grammar")
