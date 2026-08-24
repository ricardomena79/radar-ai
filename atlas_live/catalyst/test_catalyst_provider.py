"""Tests de catalyst_provider.py (2026-08-23). Sin red -- solo construcción."""

from atlas_live.catalyst import catalyst_provider as cp
from atlas_live.data_fusion.finnhub_provider import FinnhubProvider


def test_sin_finnhub_api_key_devuelve_none(monkeypatch):
    # _load_dotenv_done=True evita que build_catalyst_provider() vuelva a
    # leer el .env real del repo (que sí tiene la key) y la reponga después
    # del delenv -- este test aísla solo el caso "sin key en el entorno".
    monkeypatch.setattr(cp, "_load_dotenv_done", True)
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    assert cp.build_catalyst_provider() is None


def test_con_finnhub_api_key_devuelve_finnhub_provider(monkeypatch):
    monkeypatch.setattr(cp, "_load_dotenv_done", True)
    monkeypatch.setenv("FINNHUB_API_KEY", "clave-de-prueba")
    proveedor = cp.build_catalyst_provider()
    assert isinstance(proveedor, FinnhubProvider)


if __name__ == "__main__":
    import traceback

    class _FakeMonkeypatch:
        def __init__(self):
            self._orig = {}
            self._attrs = []

        def delenv(self, key, raising=False):
            import os
            self._orig[key] = os.environ.pop(key, None)

        def setenv(self, key, value):
            import os
            self._orig[key] = os.environ.get(key)
            os.environ[key] = value

        def setattr(self, obj, name, value):
            self._attrs.append((obj, name, getattr(obj, name)))
            setattr(obj, name, value)

        def restore(self):
            import os
            for k, v in self._orig.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            for obj, name, value in self._attrs:
                setattr(obj, name, value)

    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    p = f = 0
    for fn in fns:
        mp = _FakeMonkeypatch()
        try:
            fn(mp)
            print("PASS", fn.__name__)
            p += 1
        except Exception as e:
            print("FAIL", fn.__name__, e)
            traceback.print_exc()
            f += 1
        finally:
            mp.restore()
    print(f"--- {p} passed, {f} failed ---")
