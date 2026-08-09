# ADR-0008: Checkpoint Lineage Selects Manuscript Sources

- Status: Accepted for derived manuscript projections; amended by ADR-0009

Manuscript sources follow explicit parent Checkpoint links instead of filtering
the retained branch log by step number. Rollback intentionally preserves
abandoned turns for audit, so only the selected Checkpoint's ancestry can
distinguish canonical history from an obsolete timeline and bind a projection
to one world version.

ADR-0009 clarifies that this makes Checkpoint lineage authoritative **for
selecting projection sources**. Manuscript prose and Wiki content do not become
World Truth and are not required to restore or continue a World Session.
