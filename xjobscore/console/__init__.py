"""Console control plane for the unified xjobscore runtime.

The console stages its own inspection results in a dedicated PostgreSQL
schema (``console``). In the core pipeline's ``public`` schema its policy
layer (``policy.py``) writes only the operator-owned fields (``feed.polling``,
``feed.interval_s``, ``feed.paused_at``, ``scheduler_control.running``)
and explicit feed imports; it never touches feed runtime columns (etag,
res_hash, strikes, hold_until) -- those stay run.py's workers' alone to write.

The web package contains routes and views; ``xjobscore.runtime.UnifiedRuntime``
owns scheduling, workers, network clients, and lifecycle. Importing this
package therefore has no application-construction side effect.

``token_heartbeat.TokenHeartbeatRunner`` crosses the same boundary on a
smaller scale, and is on by default: every six hours it probes a batch of
inventory tokens for liveness and promotes the confirmed-live ones into
``feed``. Its runtime pause is ``token_heartbeat.paused``, driven from the
Tokens page.
"""
