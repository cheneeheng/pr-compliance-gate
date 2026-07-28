"""PR Compliance Gate — agentic PR policy review pipeline."""

from importlib.metadata import version

# Single source of truth is pyproject's `project.version`; never duplicate the string here.
__version__ = version("pr-compliance-gate")
