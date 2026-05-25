from telegram.ca_extractor import extract_solana_addresses, is_valid_solana_address


USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
SOL_MINT = "So11111111111111111111111111111111111111112"


def test_extracts_single_solana_address() -> None:
    text = f"CA: {USDC_MINT} ape now"

    assert extract_solana_addresses(text) == [USDC_MINT]


def test_extracts_multiple_unique_addresses_in_order() -> None:
    text = f"{SOL_MINT}\nround 2 {USDC_MINT}\nagain {SOL_MINT}"

    assert extract_solana_addresses(text) == [SOL_MINT, USDC_MINT]


def test_ignores_evm_addresses() -> None:
    text = "ignore 0x742d35Cc6634C0532925a3b844Bc454e4438f44e"

    assert extract_solana_addresses(text) == []


def test_rejects_invalid_base58_characters() -> None:
    assert not is_valid_solana_address("O0Il" * 10)


def test_rejects_base58_that_does_not_decode_to_32_bytes() -> None:
    assert not is_valid_solana_address("22222222222222222222222222222222")
