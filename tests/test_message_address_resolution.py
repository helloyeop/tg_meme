from types import SimpleNamespace

from app.pipeline import MessagePipeline

PAIR_ADDRESS = "4w2cysotx6czaugmmwg13hdpy4qemg2czekyeqyk9ama"
TOKEN_ADDRESS = "5UUH9RTDiSpq6HKS6bp4NdU9PNJpXRXuiw6ShBTBhgH2"


def test_message_address_resolution_resolves_dexscreener_urls() -> None:
    pipeline = MessagePipeline()
    pipeline.data_sources = SimpleNamespace(
        dexscreener=SimpleNamespace(
            resolve_solana_pair_identifier=lambda identifier: {PAIR_ADDRESS: TOKEN_ADDRESS}.get(
                identifier
            )
        )
    )

    addresses = pipeline._extract_message_addresses(
        f"https://dexscreener.com/solana/{PAIR_ADDRESS}"
    )

    assert addresses == [TOKEN_ADDRESS]
