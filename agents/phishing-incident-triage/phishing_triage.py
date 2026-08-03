"""Deterministic phishing-risk assessment engine.

Owns the detection knowledge: URL analysis heuristics, sender-reputation
tables, social-engineering pattern lists, authentication-result scoring,
and a composite risk rubric.  No network calls, no model invocations —
every input produces a result through pure logic.

The caller is ``agent_main.py`` (the protocol adapter).  This module
imports nothing about the wire protocol and can be tested directly.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Knowledge tables
# ---------------------------------------------------------------------------

# High-risk top-level domains commonly used in phishing campaigns.
SUSPICIOUS_TLDS: frozenset[str] = frozenset(
    {
        ".tk",
        ".ml",
        ".ga",
        ".cf",
        ".gq",
        ".buzz",
        ".xyz",
        ".top",
        ".work",
        ".click",
        ".link",
        ".info",
        ".online",
        ".site",
        ".club",
        ".icu",
        ".loan",
        ".racing",
        ".download",
        ".stream",
        ".accountant",
        ".science",
        ".party",
        ".gdn",
        ".bid",
        ".win",
        ".review",
        ".date",
        ".faith",
        ".men",
        ".tokyo",
        ".kim",
        ".ninja",
    }
)

# Known URL shortener domains (used to hide real destinations).
URL_SHORTENERS: frozenset[str] = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "ow.ly",
        "is.gd",
        "buff.ly",
        "adf.ly",
        "bl.ink",
        "lnkd.in",
        "rb.gy",
        "cutt.ly",
        "shorturl.at",
        "t.ly",
        "v.gd",
        "x.co",
        "dwz.cn",
        "qrb.cl",
    }
)

# Free email providers whose presence raises the sender-suspicion score.
FREE_EMAIL_PROVIDERS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "aol.com",
        "protonmail.com",
        "proton.me",
        "mail.com",
        "zoho.com",
        "yandex.com",
        "gmx.com",
        "fastmail.com",
        "icloud.com",
        "live.com",
        "msn.com",
        "163.com",
        "126.com",
        "qq.com",
        "rediffmail.com",
    }
)

# Urgency / pressure keywords (English, lower-cased).
URGENCY_KEYWORDS: frozenset[str] = frozenset(
    {
        "urgent",
        "immediate",
        "immediately",
        "act now",
        "expires today",
        "last chance",
        "final warning",
        "suspended",
        "locked",
        "verify now",
        "confirm your account",
        "unusual activity",
        "unauthorized access",
        "account will be closed",
        "respond within",
        "failure to comply",
        "dear customer",
        "dear user",
        "dear sir/madam",
        "attention required",
        "security alert",
        "security notice",
        "action required",
        "your account has been compromised",
        "password expired",
        "login attempt",
        "failed login",
        "unusual sign-in",
        "reset your password",
        "update your information",
        "verify your identity",
        "confirm your identity",
        "complete your registration",
        "confirm your email",
        "wire transfer",
        "invoice attached",
        "payment overdue",
        "outstanding balance",
        "past due",
        "overdue notice",
        "congratulations you have won",
        "you have been selected",
        "claim your prize",
        "free gift",
        "limited time offer",
    }
)

# Social-engineering technique labels mapped to detection patterns.
#
# Every term is word-bounded so ordinary text does not false-positive:
# "first quarter" must not match `irs`, "as a courtesy" must not match
# `court`.  Terms are bounded per-term rather than with one blanket
# `\b(...)\b`: `bank` keeps a left-only boundary so it still matches
# inside "banking", which is a legitimate hit worth keeping.
SOCIAL_ENGINEERING_PATTERNS: dict[str, list[re.Pattern[str]]] = {
    "authority_impersonation": [
        re.compile(r"\b(?:ceo|cfo|cto|director|manager|president|chairman|board)\b", re.I),
        re.compile(r"\b(?:irs|fbi|police|government|tax authority|court)\b", re.I),
        re.compile(
            r"\b(?:microsoft|apple|google|amazon|paypal|netflix|facebook|instagram)\b", re.I
        ),
        re.compile(r"\bbank|\bcredit union\b|\bfinancial institution\b", re.I),
    ],
    "urgency_fear": [
        re.compile(r"\b(?:will be (?:closed|suspended|terminated|disabled|locked))\b", re.I),
        re.compile(r"\b(?:immediate(?:ly)?|within \d+ hours?|right away)\b", re.I),
        re.compile(r"\b(?:legal action|lawsuit|arrest warrant|criminal)\b", re.I),
        re.compile(r"\b(?:failure to (?:act|respond|comply|verify))\b", re.I),
    ],
    "greed": [
        re.compile(
            r"\b(?:you (?:have )?won|congratulations|claim (?:your |the )?(?:prize|reward|bonus))\b",
            re.I,
        ),
        re.compile(r"\b(?:free (?:gift|money|vacation|iPhone|laptop|card))\b", re.I),
        re.compile(r"\b(?:inheritance|lottery|million|billion|crypto.*airdrop)\b", re.I),
    ],
    "curiosity_bait": [
        re.compile(r"\b(?:open (?:attached|this)|click (?:here|below|the link))\b", re.I),
        re.compile(
            r"\b(?:see (?:attached|below|the attached)|view (?:document|file|message))\b", re.I
        ),
        re.compile(r"\b(?:shared (?:a )?(?:document|file|photo) with you)\b", re.I),
    ],
    "pretexting": [
        re.compile(r"\b(?:i am (?:the |a )?(?:prince|minister|official|lawyer))\b", re.I),
        re.compile(r"\b(?:confidential|private|secret|do not (?:share|tell))\b", re.I),
        re.compile(r"\b(?:trust me|on my honor|i promise)\b", re.I),
    ],
}

# File extensions that run code on open — genuinely executable payloads.
EXECUTABLE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".exe",
        ".scr",
        ".com",
        ".bat",
        ".cmd",
        ".vbs",
        ".vbe",
        ".js",
        ".jse",
        ".wsf",
        ".wsh",
        ".ps1",
        ".msi",
        ".msp",
        ".mst",
        ".cpl",
        ".hta",
        ".inf",
        ".reg",
        ".rgs",
        ".sct",
        ".shb",
        ".shs",
        ".lnk",
        ".pif",
        ".application",
        ".gadget",
        ".msh",
        ".msh1",
        ".msh2",
        ".mshxml",
        ".msh1xml",
        ".msh2xml",
        ".psc1",
        ".psc2",
        ".psm1",
    }
)

# Office formats that can carry active macros or embedded objects.  The
# macro-free variants (.docx, .xlsx, .pptx, .xltx, .sldx) are deliberately
# absent: a plain Office document is not a payload, and flagging every
# invoice as an "executable" would drown real signals.
MACRO_DOCUMENT_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".asd",
        ".rtf",
        ".doc",
        ".docm",
        ".xls",
        ".xlsm",
        ".ppt",
        ".pptm",
        ".ppam",
        ".sldm",
        ".dotm",
        ".xltm",
    }
)

# Combined set of extension groups treated as dangerous attachments.
DANGEROUS_EXTENSIONS: frozenset[str] = EXECUTABLE_EXTENSIONS | MACRO_DOCUMENT_EXTENSIONS

# File extensions that are suspicious but not inherently dangerous.
SUSPICIOUS_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".zip",
        ".rar",
        ".7z",
        ".tar",
        ".gz",
        ".cab",
        ".iso",
        ".img",
        ".vhd",
        ".vhdx",
        ".ones",
        ".ace",
        ".arj",
    }
)

# ---------------------------------------------------------------------------
# Scoring weights (deterministic rubric)
# ---------------------------------------------------------------------------

WEIGHTS = {
    "url_score": 25,
    "sender_score": 20,
    "header_score": 15,
    "content_score": 25,
    "attachment_score": 15,
}

# ---------------------------------------------------------------------------
# Analysis functions
# ---------------------------------------------------------------------------


def _inspect_url(url: str) -> tuple[int, list[str]]:
    """Score a single URL.  Returns (score, reasons)."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    host_lower = host.lower()
    reasons: list[str] = []
    score = 0

    # IP address as hostname
    if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", host):
        reasons.append("IP address as hostname")
        score += 8

    # URL shortener
    if host_lower in URL_SHORTENERS:
        reasons.append("URL shortener detected")
        score += 5

    # Suspicious TLD
    for tld in SUSPICIOUS_TLDS:
        if host_lower.endswith(tld):
            reasons.append(f"suspicious TLD ({tld})")
            score += 6
            break

    # Credential injection: userinfo (user@host) can hide the real
    # host.  Test the authority component only — an '@' in a query
    # parameter (e.g. ?email=alice@corp.com) is ordinary.
    if parsed.username:
        reasons.append("@ sign in URL (credential injection)")
        score += 8

    # Punycode / homograph (contains xn--)
    if "xn--" in host_lower:
        reasons.append("punycode / internationalized domain")
        score += 7

    # Excessive subdomains
    parts = host.split(".")
    if len(parts) > 4:
        reasons.append(f"excessive subdomains ({len(parts)} levels)")
        score += 4

    # URL path contains login/auth keywords, matched against whole path
    # segments so ordinary route words like "account" or "invoices" do
    # not score.
    path_lower = parsed.path.lower()
    if re.search(
        r"(?:^|/)(?:login|signin|sign-in|verify|confirm|secure|update|password|auth)(?:[./]|$)",
        path_lower,
    ):
        reasons.append("path contains login/auth keywords")
        score += 5

    # Mismatched display text vs href (can't fully detect without context,
    # but we flag obviously wrong patterns)
    url_lower = url.strip().lower()
    if url_lower.startswith("http://") and "secure" in url_lower:
        reasons.append("http with 'secure' in URL")
        score += 6

    return score, reasons


def _analyse_urls(urls: list[str]) -> tuple[int, list[dict[str, Any]], list[str]]:
    """Analyse embedded URLs and return (score, suspicious_urls, warnings)."""
    if not urls:
        return 0, [], []

    score = 0
    suspicious: list[dict[str, Any]] = []
    warnings: list[str] = []

    for url in urls:
        url_score, reasons = _inspect_url(url)
        score += url_score
        if reasons:
            suspicious.append({"url": url, "reasons": reasons})

    # Cap the URL score contribution at the weight
    score = min(score, WEIGHTS["url_score"])

    if suspicious:
        warnings.append(f"{len(suspicious)} of {len(urls)} URLs flagged as suspicious")

    return score, suspicious, warnings


def _analyse_sender(
    sender: str,
    reply_to: str | None,
    body: str,
) -> tuple[int, list[str], list[str]]:
    """Analyse sender address and reply-to mismatch.

    Returns (score, sender_inconsistencies, warnings).
    """
    score = 0
    inconsistencies: list[str] = []
    warnings: list[str] = []

    sender_lower = sender.strip().lower()

    # Extract domain from sender
    sender_domain = ""
    if "@" in sender_lower:
        sender_domain = sender_lower.split("@", 1)[1]

    # Free email provider
    if sender_domain in FREE_EMAIL_PROVIDERS:
        score += 3
        warnings.append(f"sender uses free email provider ({sender_domain})")

    # Sender domain not matching any mentioned organization
    org_patterns = re.findall(r"(?:from|on behalf of|@)(\w[\w.-]+\.\w{2,})", body, re.I)
    mentioned_domains = {d.lower() for d in org_patterns}
    if (
        sender_domain
        and mentioned_domains
        and not any(
            sender_domain == d or sender_domain.endswith("." + d) or d.endswith("." + sender_domain)
            for d in mentioned_domains
        )
    ):
        score += 4
        inconsistencies.append(
            f"sender domain '{sender_domain}' does not match any domain mentioned in body"
        )

    # Reply-to mismatch
    if reply_to:
        reply_to_lower = reply_to.strip().lower()
        reply_domain = ""
        if "@" in reply_to_lower:
            reply_domain = reply_to_lower.split("@", 1)[1]

        if reply_domain and sender_domain and reply_domain != sender_domain:
            score += 7
            inconsistencies.append(
                f"reply-to domain '{reply_domain}' differs from sender domain '{sender_domain}'"
            )

    # Display-name spoofing and suspicious local parts
    local_score, local_incs, local_warns = _analyse_sender_local(sender, sender_domain)
    score += local_score
    inconsistencies.extend(local_incs)
    warnings.extend(local_warns)

    score = min(score, WEIGHTS["sender_score"])
    return score, inconsistencies, warnings


def _analyse_sender_local(sender: str, sender_domain: str) -> tuple[int, list[str], list[str]]:
    """Display-name spoofing and suspicious local-part heuristics."""
    score = 0
    inconsistencies: list[str] = []
    warnings: list[str] = []
    sender_lower = sender.strip().lower()

    # Display name spoofing: quoted display name with different domain
    display_match = re.match(r'^"([^"]+)"\s*<', sender)
    if display_match:
        display_name = display_match.group(1)
        # Check if display name contains a domain that differs
        if "@" in display_name:
            display_domain = display_name.split("@")[-1].strip().lower()
            if display_domain and sender_domain and display_domain != sender_domain:
                score += 5
                inconsistencies.append(
                    f"display name contains domain '{display_domain}' "
                    f"but sender is '{sender_domain}'"
                )

    # Numeric or random-looking local part
    if "@" in sender_lower:
        local_part = sender_lower.split("@", 1)[0]
        # All numeric
        if local_part.isdigit():
            score += 2
            warnings.append("sender local part is all numeric")
        # Very long random string
        elif len(local_part) > 20 and not re.match(r"^[a-z]+[._-]?[a-z]+$", local_part):
            score += 2
            warnings.append("sender local part looks randomly generated")

    return score, inconsistencies, warnings


def _analyse_headers(
    spf: str | None,
    dkim: str | None,
    dmarc: str | None,
) -> tuple[int, list[dict[str, str]], list[str]]:
    """Analyse authentication results.  Returns (score, auth_findings, warnings)."""
    score = 0
    findings: list[dict[str, str]] = []
    warnings: list[str] = []

    def _parse_result(raw: str | None) -> str:
        if raw is None:
            return "missing"
        return raw.strip().lower()

    spf_r = _parse_result(spf)
    dkim_r = _parse_result(dkim)
    dmarc_r = _parse_result(dmarc)

    # SPF
    if spf_r == "missing":
        # Absence of data is the caller's gap, not the message's behaviour:
        # keep reporting it in warnings but do not add it to the score.
        findings.append({"check": "SPF", "result": "missing", "severity": "low"})
        warnings.append("SPF result not provided")
    elif "fail" in spf_r or "softfail" in spf_r:
        score += 6
        findings.append({"check": "SPF", "result": spf_r, "severity": "high"})
    elif "neutral" in spf_r:
        score += 2
        findings.append({"check": "SPF", "result": spf_r, "severity": "low"})
    else:
        findings.append({"check": "SPF", "result": spf_r, "severity": "none"})

    # DKIM
    if dkim_r == "missing":
        findings.append({"check": "DKIM", "result": "missing", "severity": "low"})
        warnings.append("DKIM result not provided")
    elif "fail" in dkim_r:
        score += 6
        findings.append({"check": "DKIM", "result": dkim_r, "severity": "high"})
    elif "neutral" in dkim_r:
        score += 2
        findings.append({"check": "DKIM", "result": dkim_r, "severity": "low"})
    else:
        # "none" means unsigned or no policy record — absence, not failure —
        # treated exactly as the SPF branch above treats it.
        findings.append({"check": "DKIM", "result": dkim_r, "severity": "none"})

    # DMARC
    if dmarc_r == "missing":
        findings.append({"check": "DMARC", "result": "missing", "severity": "low"})
        warnings.append("DMARC result not provided")
    elif "fail" in dmarc_r:
        score += 5
        findings.append({"check": "DMARC", "result": dmarc_r, "severity": "medium"})
    else:
        # "none" is absence, not failure — same as SPF and DKIM above.
        findings.append({"check": "DMARC", "result": dmarc_r, "severity": "none"})

    score = min(score, WEIGHTS["header_score"])
    return score, findings, warnings


def _analyse_content(subject: str, body: str) -> tuple[int, list[str], list[str]]:
    """Analyse email subject and body for phishing indicators.

    Returns (score, social_engineering_techniques, indicators).
    """
    # Combine subject and body for analysis
    combined = f"{subject}\n{body}" if subject else body
    if not combined.strip():
        return 0, [], []

    score = 0
    techniques: list[str] = []
    indicators: list[str] = []

    combined_lower = combined.lower()

    # Urgency keywords
    urgency_hits = [kw for kw in URGENCY_KEYWORDS if kw in combined_lower]
    if urgency_hits:
        kw_score = min(len(urgency_hits) * 2, 10)
        score += kw_score
        indicators.append(
            f"urgency/pressure keywords detected: {', '.join(sorted(urgency_hits)[:5])}"
        )

    # Social engineering patterns
    for technique, patterns in SOCIAL_ENGINEERING_PATTERNS.items():
        for pat in patterns:
            if pat.search(combined):
                techniques.append(technique)
                score += 4
                break  # one match per technique is enough

    # Credential harvesting: requests for passwords, SSNs, etc.
    cred_patterns = [
        (r"(password|passwd|pwd)\s*[:=]", "requests password"),
        (r"(social security|ssn)\s*(number)?\s*[:=]", "requests SSN"),
        (r"(credit card|card number)\s*(number)?\s*[:=]", "requests credit card"),
        (r"(bank account|routing number)\s*[:=]", "requests bank details"),
        (r"(pin\s*code|pin\s*number)\s*[:=]", "requests PIN"),
        (r"(date of birth|dob)\s*[:=]", "requests date of birth"),
    ]
    for pat, desc in cred_patterns:
        if re.search(pat, combined, re.I):
            score += 8
            indicators.append(f"credential harvesting: {desc}")

    # HTML forms in body (common in phishing)
    if re.search(r"<form[^>]*>", body, re.I):
        score += 5
        indicators.append("HTML form embedded in email body")

    # Data URI / embedded images for tracking
    if "data:image" in combined_lower:
        score += 2
        indicators.append("data URI image detected (possible tracking pixel)")

    # Excessive links
    link_count = len(re.findall(r"https?://", combined))
    if link_count > 5:
        score += 3
        indicators.append(f"excessive links ({link_count} found)")

    # Base64 encoded content (common in obfuscation)
    if re.search(r"base64[,;]", combined_lower):
        score += 3
        indicators.append("base64-encoded content detected")

    # Invisible text / zero-width characters
    if re.search(r"[\u200b\u200c\u200d\ufeff]", body):
        score += 4
        indicators.append("invisible/zero-width characters detected")

    score = min(score, WEIGHTS["content_score"])
    return score, techniques, indicators


def _analyse_attachments(attachments: list[str]) -> tuple[int, list[str], list[str]]:
    """Analyse attachment names for dangerous file types.

    Returns (score, dangerous_attachments, warnings).
    """
    if not attachments:
        return 0, [], []

    score = 0
    dangerous: list[str] = []
    warnings: list[str] = []

    for name in attachments:
        name_lower = name.lower().strip()

        # Double extension trick (e.g. document.pdf.exe)
        base_exts = name_lower.split(".")
        if len(base_exts) > 2:
            final_ext = "." + base_exts[-1]
            if final_ext in DANGEROUS_EXTENSIONS:
                score += 10
                dangerous.append(f"{name} (double extension with dangerous payload)")
                continue

        # Check final extension
        ext = ""
        dot_pos = name_lower.rfind(".")
        if dot_pos >= 0:
            ext = name_lower[dot_pos:]

        if ext in DANGEROUS_EXTENSIONS:
            score += 8
            label = (
                "dangerous executable type"
                if ext in EXECUTABLE_EXTENSIONS
                else "macro-capable document"
            )
            dangerous.append(f"{name} ({label})")
        elif ext in SUSPICIOUS_EXTENSIONS:
            score += 3
            warnings.append(f"{name} (archive type — may contain payloads)")
        elif not ext:
            score += 3
            warnings.append(f"{name} (no file extension)")

    score = min(score, WEIGHTS["attachment_score"])
    return score, dangerous, warnings


def _compute_severity(risk_score: int) -> str:
    """Map a 0-100 risk score to a severity label."""
    if risk_score >= 75:
        return "critical"
    if risk_score >= 50:
        return "high"
    if risk_score >= 25:
        return "medium"
    return "low"


def _compute_classification(
    risk_score: int,
    techniques: list[str],
    indicators: list[str],
    suspicious_urls: list[dict[str, Any]],
) -> str:
    """Classify the message.

    The bands mirror ``_compute_severity`` so the two can never contradict:
    every 50-74 message is ``suspicious_phishing`` regardless of which
    signals accumulated the points.  Only the critical band is refined
    further (credential harvesting, spear phishing).
    """
    if risk_score >= 75:
        if any("credential" in i for i in indicators):
            return "credential_harvesting"
        if techniques and suspicious_urls:
            return "spear_phishing"
        return "likely_phishing"
    if risk_score >= 50:
        return "suspicious_phishing"
    if risk_score >= 25:
        return "low_risk_suspicious"
    return "likely_legitimate"


def _compute_confidence(
    risk_score: int,
    url_count: int,
    has_auth: bool,
    body_length: int,
) -> float:
    """Estimate confidence in the assessment (0.0-1.0).

    More signals and more content → higher confidence.  Very short or
    very long bodies reduce it slightly (harder to assess).
    """
    base = 0.5

    # More URLs give more signal
    if url_count > 0:
        base += 0.1
    if url_count > 3:
        base += 0.05

    # Authentication results add confidence
    if has_auth:
        base += 0.1

    # Body length sweet spot
    if 50 < body_length < 2000:
        base += 0.1
    elif body_length <= 10:
        base -= 0.1

    # Extreme scores are more confident (clear-cut cases)
    if risk_score > 70 or risk_score < 15:
        base += 0.05

    return round(min(max(base, 0.1), 0.95), 2)


def _recommend_actions(
    risk_score: int,
    severity: str,
    classification: str,
    dangerous_attachments: list[str],
    has_auth_fail: bool,
) -> list[str]:
    """Generate recommended response actions."""
    actions: list[str] = []

    if severity == "critical":
        actions.append("IMMEDIATE: Do not interact with this message")
        actions.append("Quarantine the email in the user's mailbox")
        actions.append("Report to security team for incident response")
        if dangerous_attachments:
            actions.append("Block attachment hash at email gateway")
    elif severity == "high":
        actions.append("Do not click any links or open attachments")
        actions.append("Report to security team for review")
        actions.append("Consider blocking the sender domain")
    elif severity == "medium":
        actions.append("Exercise caution — do not click links or download attachments")
        actions.append("Verify sender through a separate communication channel")
    else:
        actions.append("Low risk — standard precautions apply")

    if has_auth_fail:
        actions.append("Notify sender's domain owner about authentication failure")

    if classification == "credential_harvesting":
        actions.append("Alert users who may have entered credentials")
        actions.append("Rotate any potentially compromised credentials")

    if "spear" in classification:
        actions.append("Check if other employees received similar targeted messages")

    return actions


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def triage_email(
    subject: str,
    sender: str,
    reply_to: str | None,
    body: str,
    urls: list[str],
    attachments: list[str],
    spf: str | None,
    dkim: str | None,
    dmarc: str | None,
) -> dict[str, Any]:
    """Analyse a suspicious email and return a structured risk assessment.

    This is the deterministic core.  Every input produces a result through
    pure logic — no network calls, no model invocations.
    """
    # --- URL analysis ---
    url_score, suspicious_urls, url_warnings = _analyse_urls(urls)

    # --- Sender analysis ---
    sender_score, sender_inconsistencies, sender_warnings = _analyse_sender(sender, reply_to, body)

    # --- Header / authentication analysis ---
    header_score, auth_findings, header_warnings = _analyse_headers(spf, dkim, dmarc)

    # --- Content analysis ---
    content_score, techniques, content_indicators = _analyse_content(subject, body)

    # --- Attachment analysis ---
    attachment_score, dangerous_attachments, attachment_warnings = _analyse_attachments(attachments)

    # --- Composite risk score ---
    raw_score = url_score + sender_score + header_score + content_score + attachment_score
    risk_score = min(max(raw_score, 0), 100)

    # --- Derived fields ---
    severity = _compute_severity(risk_score)
    classification = _compute_classification(
        risk_score, techniques, content_indicators, suspicious_urls
    )

    has_auth = any(f["result"] != "missing" for f in auth_findings)
    has_auth_fail = any(f["result"] in ("fail", "softfail") for f in auth_findings)

    confidence = _compute_confidence(
        risk_score,
        url_count=len(urls),
        has_auth=has_auth,
        body_length=len(body),
    )

    recommended_actions = _recommend_actions(
        risk_score, severity, classification, dangerous_attachments, has_auth_fail
    )

    # --- Evidence summary ---
    evidence_parts: list[str] = []
    if url_score:
        evidence_parts.append(f"URL analysis scored {url_score}/{WEIGHTS['url_score']}")
    if sender_score:
        evidence_parts.append(f"Sender analysis scored {sender_score}/{WEIGHTS['sender_score']}")
    if header_score:
        evidence_parts.append(f"Header analysis scored {header_score}/{WEIGHTS['header_score']}")
    if content_score:
        evidence_parts.append(f"Content analysis scored {content_score}/{WEIGHTS['content_score']}")
    if attachment_score:
        evidence_parts.append(
            f"Attachment analysis scored {attachment_score}/{WEIGHTS['attachment_score']}"
        )

    # --- Analyst summary ---
    if risk_score >= 75:
        summary = (
            f"HIGH CONFIDENCE PHISHING: This message exhibits strong indicators "
            f"of a phishing attempt (score {risk_score}/100). "
            f"Classification: {classification}. "
            f"Immediate action recommended."
        )
    elif risk_score >= 50:
        summary = (
            f"SUSPICIOUS: This message shows moderate phishing indicators "
            f"(score {risk_score}/100). Classification: {classification}. "
            f"Further investigation recommended."
        )
    elif risk_score >= 25:
        summary = (
            f"LOW RISK: Some suspicious elements detected "
            f"(score {risk_score}/100). Standard precautions advised."
        )
    else:
        summary = (
            f"LIKELY LEGITIMATE: Minimal phishing indicators detected "
            f"(score {risk_score}/100). No significant concerns identified."
        )

    # --- Warnings ---
    all_warnings = url_warnings + sender_warnings + header_warnings + attachment_warnings

    return {
        "risk_score": risk_score,
        "severity": severity,
        "classification": classification,
        "detected_indicators": content_indicators,
        "suspicious_urls": suspicious_urls,
        "sender_inconsistencies": sender_inconsistencies,
        "social_engineering_techniques": techniques,
        "authentication_findings": auth_findings,
        "evidence": evidence_parts,
        "recommended_actions": recommended_actions,
        "analyst_summary": summary,
        "confidence": confidence,
        "warnings": all_warnings,
    }
