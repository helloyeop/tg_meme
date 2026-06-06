import re
from collections.abc import Iterable

BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58_INDEX = {char: index for index, char in enumerate(BASE58_ALPHABET)}
SOLANA_ADDRESS_RE = re.compile(
    rf"(?<![{BASE58_ALPHABET}])([{BASE58_ALPHABET}]{{32,44}})(?![{BASE58_ALPHABET}])"
)
EVM_ADDRESS_RE = re.compile(r"\b0x[a-fA-F0-9]{40}\b")
DEXSCREENER_SOLANA_URL_RE = re.compile(
    rf"https?://(?:www\.)?dexscreener\.com/solana/([{BASE58_ALPHABET}]{{32,44}})"
    r"(?:[/?#][^\s]*)?",
    re.IGNORECASE,
)


def is_valid_solana_address(value: str) -> bool:
    candidate = value.strip()
    if not 32 <= len(candidate) <= 44:
        return False
    if EVM_ADDRESS_RE.fullmatch(candidate):
        return False
    try:
        decoded = b58decode(candidate)
    except ValueError:
        return False
    return len(decoded) == 32


def b58decode(value: str) -> bytes:
    number = 0
    for char in value:
        try:
            digit = BASE58_INDEX[char]
        except KeyError as exc:
            raise ValueError(f"invalid base58 character: {char}") from exc
        number = number * 58 + digit

    combined = number.to_bytes((number.bit_length() + 7) // 8, byteorder="big")
    leading_zero_count = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading_zero_count + combined


def extract_solana_addresses(text: str | None) -> list[str]:
    if not text:
        return []

    evm_spans = list(_spans(EVM_ADDRESS_RE.finditer(text)))
    dexscreener_spans = list(_spans(DEXSCREENER_SOLANA_URL_RE.finditer(text)))
    addresses: list[str] = []
    seen: set[str] = set()

    for match in SOLANA_ADDRESS_RE.finditer(text):
        candidate = match.group(1)
        if _inside_spans(match.start(), match.end(), evm_spans + dexscreener_spans):
            continue
        if not is_valid_solana_address(candidate):
            continue
        if candidate in seen:
            continue
        seen.add(candidate)
        addresses.append(candidate)

    return addresses


def extract_dexscreener_solana_identifiers(text: str | None) -> list[str]:
    if not text:
        return []

    identifiers: list[str] = []
    seen: set[str] = set()
    for match in DEXSCREENER_SOLANA_URL_RE.finditer(text):
        identifier = match.group(1)
        if identifier in seen:
            continue
        seen.add(identifier)
        identifiers.append(identifier)
    return identifiers


def _spans(matches: Iterable[re.Match[str]]) -> list[tuple[int, int]]:
    return [(match.start(), match.end()) for match in matches]


def _inside_spans(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(span_start <= start and end <= span_end for span_start, span_end in spans)
