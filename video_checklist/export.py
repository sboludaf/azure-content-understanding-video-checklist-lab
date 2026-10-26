from html import escape
import json


def provider_name(result):
    if result.get("source") == "fixture":
        return "Fixture local"
    provider = result.get("analysis_provider")
    if provider == "azure_foundry_responses":
        return "Microsoft Foundry · Azure Responses"
    if not provider or provider == "azure_content_understanding":
        return "Azure Content Understanding"
    return str(provider)


def sampling_description(sampling):
    if not isinstance(sampling, dict):
        return None
    parts = []
    for key, label in (("fps", "fotogramas/s"), ("actual_frame_count", "fotogramas analizados"), ("max_edge_px", "px de lado máximo")):
        value = sampling.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            parts.append(f"{value} {label}")
    return " · ".join(parts) or "Datos de muestreo no disponibles"


def html_report(result):
    clip = result["clip"]
    rows = []
    for step in result["checklist"]:
        evidence = []
        for e in step["events"]:
            if isinstance(e, str):
                e = next((x for x in result["events"] if x["id"] == e), {})
            evidence_status = {"observed": "Observado", "uncertain": "Incierto", "not_observed": "No observado en este vídeo"}.get(e.get("evidence_status"), "Incierto")
            evidence.append(f"{escape(str(e.get('start_ms')))}–{escape(str(e.get('end_ms')))} ms ({escape(str(e.get('time_source')))}) · <strong>{evidence_status}</strong>")
        rows.append(f"<tr><td>{escape(step['id'])}</td><td>{escape(step['label'])}</td><td>{escape(step['status'])}</td><td>{'<br>'.join(evidence)}</td></tr>")
    payload = escape(json.dumps(result, ensure_ascii=False, indent=2))
    sampling = sampling_description(result.get("frame_sampling"))
    sampling_html = f"<br>Muestreo visual: {escape(sampling)}<br>Tiempos: estimaciones del modelo sobre fotogramas; requieren revisión y no acreditan grounding temporal del servicio." if sampling else ""
    limitations = result.get("limitations", [])
    limitations_html = "<ul>" + "".join(f"<li>{escape(str(item))}</li>" for item in limitations) + "</ul>" if isinstance(limitations, list) and limitations else ""
    issue_notice = "Hay observaciones que no se pudieron confirmar. Revisa las filas inciertas y sus evidencias antes de decidir." if result['issues'] else ""
    return f'''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Informe de revisión de vídeo</title><style>body{{max-width:1000px;margin:3rem auto;padding:0 1rem;font:16px system-ui;color:#152b30;overflow-wrap:anywhere}}table{{border-collapse:collapse;width:100%}}td,th{{padding:.7rem;border:1px solid #ccd}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f5;padding:1rem}}</style><h1>Checklist de la grabación</h1><p>Fuente del resultado: <strong>{escape(result['source'])}</strong>. Una acción no observada en este vídeo no prueba que no se realizara. Este informe no certifica seguridad.</p><p>Archivo: {escape(clip['name'])}<br>SHA-256: {escape(clip['hash'])}<br>Duración: {clip['duration_ms']} ms<br>Atribución/licencia: {escape(clip['license'])}<br>Esquema: {escape(result['schema_version'])}<br>Proveedor: {escape(provider_name(result))}<br>API: {escape(result['api_version'])}<br>Modelo: {escape(str(result['model']))}<br>Versión del modelo: {escape(str(result.get('model_version', 'No disponible')))}{sampling_html}</p><table><thead><tr><th>ID</th><th>Paso</th><th>Estado IA</th><th>Evidencia temporal</th></tr></thead><tbody>{''.join(rows)}</tbody></table><h2>Limitaciones y revisión humana</h2><p>{escape(issue_notice)}</p>{limitations_html}<p>La revisión humana permanece separada de las observaciones IA. El vídeo no se incorpora al informe; los saltos se consultan en la aplicación con el archivo disponible.</p><pre>{payload}</pre></html>'''
