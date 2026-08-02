"""Ejemplo de uso de la librería de latido -- sirve como referencia para
instrumentar un proceso real de Atlas, y como script de validación
manual del Entregable 1 (ver ATLAS_MISSION_CONTROL.md).

Simula un proceso corto de varias "unidades de trabajo" (como una
validación histórica simula días), sin descargar nada real.

Uso: `python -m atlas_live.mission_control.example_usage`
"""

import time

from atlas_live.mission_control import heartbeat

TOTAL_UNITS = 5


def main() -> None:
    run_id = heartbeat.make_run_id("EXAMPLE")
    print(f"Run ID: {run_id}")

    hb = heartbeat.start(
        run_id=run_id,
        process_type="example",
        label="Ejemplo de uso del latido",
        total=TOTAL_UNITS,
        unit="unidades",
        message="Arrancando el ejemplo",
    )

    for i in range(1, TOTAL_UNITS + 1):
        time.sleep(0.3)
        hb.step(done=i, message=f"Procesando unidad {i}/{TOTAL_UNITS}")
        status = heartbeat.read_status(run_id)
        print(
            f"  [{i}/{TOTAL_UNITS}] estado={status['state']} "
            f"cpu={status['cpu_percent']}% mem={status['memory_mb']}MB "
            f"transcurrido={status['elapsed_seconds']}s eta={status['eta_seconds']}s "
            f"version={status['atlas_version']}"
        )

    hb.finish(message="Ejemplo completado")
    final_status = heartbeat.read_status(run_id)
    print(f"Estado final: {final_status['state']} -- {final_status['last_message']}")
    print(f"Archivo generado en: atlas_live/mission_control/status/{run_id}.json")


if __name__ == "__main__":
    main()
