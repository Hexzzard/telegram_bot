import httpx

URL = "https://api.telegram.org"


def test_connection(name: str, trust_env: bool) -> None:
    print(f"\nProbando: {name}")

    try:
        with httpx.Client(
            timeout=20.0,
            trust_env=trust_env,
            follow_redirects=True,
        ) as client:
            response = client.get(URL)

        print("Conexión HTTPS establecida.")
        print(f"Código HTTP: {response.status_code}")

    except httpx.HTTPError as error:
        print("La conexión falló.")
        print(f"Tipo: {type(error).__name__}")
        print(f"Detalle: {error}")


def main() -> None:
    test_connection(
        "Python usando configuración de entorno",
        trust_env=True,
    )

    test_connection(
        "Python ignorando proxies del entorno",
        trust_env=False,
    )


if __name__ == "__main__":
    main()
