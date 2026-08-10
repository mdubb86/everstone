"""Warm-keeper entrypoint (run ~4x/day by the warm-keep s6 loop-service).

Browses google.com for each authenticated profile to keep the session alive — this rotates the
short-lived __Secure-*PSIDTS cookies that go stale when idle — then re-persists. NEVER triggers a
login: a dead/absent profile is skipped (cheap durable-cookie pre-gate) and left dead until a real
tool use raises `authentication_required`. Logic lives in es.web_login.run_warm_keep.
"""
import es.web_login as wl

# Auth-target profiles to keep warm. One Google login today; add targets as consumers appear.
PROFILES = ["google"]


def main():
    for profile in PROFILES:
        try:
            result = wl.run_warm_keep(profile, read_durable=wl.read_durable_state,
                                      probe_home=wl.probe_home, persist=wl.fetch_state)
            print(f"[warm-keep] {result}", flush=True)
        except Exception as e:  # noqa: BLE001 — background job: log and move to the next profile
            print(f"[warm-keep] {profile} error: {e}", flush=True)


if __name__ == "__main__":
    main()
