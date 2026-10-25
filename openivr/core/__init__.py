"""OpenIVR Core module: Stasis multitasking IVR state machine and call services.

Import the concrete modules directly (``openivr.core.media``,
``openivr.core.service`` …).  This package intentionally re-exports nothing:
``openivr.core.flow`` imports ``openivr.core.media``, so eagerly importing
``openivr.core.context`` (which imports ``openivr.core.flow``) from here would
make those modules import each other in a cycle.
"""
