Phase | Recommended model/effort | Purpose
---|---|---
Scope and inventory | Terra | medium | Classify code, tests, data, docs, generated files, and legacy material
Subsystem reconnaissance | Terra / high | Map entry points, dependencies, data flows, tests, and responsibilities
Architecture synthesis | Sol / high | Connect subsystem findings and identify cross-cutting patterns
Difficult or high-risk areas | Sol / xhigh | Analyze concurrency, persistence, parsing correctness, security, and unclear behavior
Final critique and prioritization | Sol / xhigh | Rank risks, resolve contradictions, and produce recommendations
Mechanical verification | Terra / low or medium | Run tests, check references, confirm paths, and format the final artifacts







1. **feed**: single endpoint/board
2. **posting-feed**: source dependent representation of single role ... (source, external_id)
3. **posting**: "logical role" on the board (multiple observations appended here).
4. **key**: evidence why the two are considered identical.


Create a unified posting table that aggregate duplicate feed-post into a single posting (with assertions)

Status Resolution
---
Resolve only based on authoritative sources (not aggregators).
1. **Open**: At least once source origin observes it
2. **Unlisted**: AT least once source origin vanished (after creation)
3. **Closed**: All origin unlisted or at least one actually explicitely says its closed.


```python
poll_run_item(
    poll_run_id,
    feed_id,
    external_id,
    raw_document_id nullable,
    primary key (poll_run_id, external_id)
)
```
- prev snapshot IDs - curr snapshot IDs