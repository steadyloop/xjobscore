"""Common Crawl -> ATS board tokens -> feed_ats.

Stages are separate commands on purpose: the scan is slow and network-bound,
the probe is rate-limited, and the binder is the only one that writes company
data. Each writes its output to the working directory so a failure in a later
stage never costs you an earlier one.
"""
