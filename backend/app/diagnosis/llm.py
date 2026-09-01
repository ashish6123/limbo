import anthropic

from app.diagnosis.schemas import DiagnosisResult, EvidenceBundle

MODEL = "claude-opus-5"

_SYSTEM_PROMPT = """You are the root-cause diagnosis agent for Limbo, a payment reconciliation system.
You are given observable evidence about one transaction — webhook delivery records, payment/refund
status, and timestamps. You do not know what actually went wrong upstream; you must infer it from
this evidence alone. Classify the root cause, state your confidence, write a short human-readable
narrative explaining what you inferred and why, and recommend an action. Never recommend a
money-moving action directly — recommend it in plain language; a separate policy engine decides
whether to actually execute it."""


async def diagnose_llm(evidence: EvidenceBundle, client: anthropic.AsyncAnthropic) -> DiagnosisResult | None:
    """Returns None on any failure — missing key, rate limit, malformed output —
    so the caller can fall back to the deterministic classifier. Every LLM call
    site in this system must degrade gracefully; this is that degradation point."""
    try:
        response = await client.messages.parse(
            model=MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": evidence.model_dump_json(indent=2)}],
            output_format=DiagnosisResult,
        )
    except anthropic.RateLimitError:
        return None
    except anthropic.APIStatusError:
        return None
    except anthropic.APIConnectionError:
        return None
    except Exception:
        return None

    return response.parsed_output
