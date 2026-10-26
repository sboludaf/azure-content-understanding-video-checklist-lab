'use strict';

(() => {
  const STORAGE_KEY = 'mvp-video-checklist:2026-10-15:last-job';
  const PENDING_KEY = 'mvp-video-checklist:2026-10-15:pending-request';
  const ACTIVE = new Set(['queued', 'validating', 'submitting', 'processing']);
  const labels = { observed: 'Observado', not_observed: 'No observado en este vídeo', uncertain: 'Incierto' };
  const defaults = [
    { id: 'paso_1', label: 'Preparar los materiales' },
    { id: 'paso_2', label: 'Realizar la acción principal' },
    { id: 'paso_3', label: 'Mostrar el resultado' },
  ];
  const $ = id => document.getElementById(id);
  const state = {
    config: null, file: null, fileValid: false, duration: null, previewUrl: null,
    selectedVersion: 0, job: null, submitting: false, reviewSaving: false,
    loadingSample: false, pollTimer: null, polling: false, requestKey: null,
    pendingSubmission: false, renderedResultId: null, recovering: false, reviewDirty: false,
  };

  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function storedRead(key) { try { return localStorage.getItem(key); } catch { return null; } }
  function storedWrite(key, value) { try { value === null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch { /* Private browsing may disallow storage. */ } }
  function notify(message, type = 'error') {
    $('message').className = `notice${type === 'info' ? ' notice-info' : type === 'warning' ? ' notice-amber' : ''}`;
    $('message').textContent = message;
    $('message').hidden = false;
  }
  function clearNotice() { $('message').hidden = true; $('message').textContent = ''; }
  function stringify(value) { return value == null ? 'No disponible' : typeof value === 'object' ? JSON.stringify(value) : String(value); }
  function formatTime(milliseconds, includeMilliseconds = false) {
    if (typeof milliseconds !== 'number' || !Number.isFinite(milliseconds) || milliseconds < 0) return 'Sin tiempo válido';
    const seconds = Math.floor(milliseconds / 1000);
    const fraction = includeMilliseconds ? String(Math.floor(milliseconds % 1000)).padStart(3, '0').replace(/0+$/, '') : '';
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}${fraction ? `,${fraction}` : ''}`;
  }
  function validTime(event, durationMs) {
    return typeof event.start_ms === 'number' && typeof event.end_ms === 'number'
      && Number.isFinite(event.start_ms) && Number.isFinite(event.end_ms)
      && event.start_ms >= 0 && event.end_ms > event.start_ms
      && typeof durationMs === 'number' && Number.isFinite(durationMs) && event.end_ms <= durationMs;
  }
  async function request(path, options = {}) {
    const response = await fetch(path, { credentials: 'same-origin', ...options });
    let data;
    try { data = await response.json(); } catch { throw new Error('El servidor devolvió una respuesta no válida. Conserva la operación y consulta su estado.'); }
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : 'No se pudo completar la solicitud. Revisa los datos y vuelve a intentarlo.');
      error.status = response.status;
      throw error;
    }
    return data;
  }
  function invalidateRequest() {
    if (!state.submitting && !state.pendingSubmission) { state.requestKey = null; storedWrite(PENDING_KEY, null); }
  }
  function updateControls() {
    const busy = state.submitting || state.recovering || state.loadingSample || (state.job && ACTIVE.has(state.job.status)) || state.pendingSubmission;
    const unresolved = state.job?.status === 'uncertain';
    $('input-fields').disabled = Boolean(busy || state.job || unresolved);
    $('sample-button').disabled = Boolean(busy || state.job || unresolved || state.loadingSample);
    const configured = state.config && state.config.cloud_enabled;
    $('analyze-button').disabled = Boolean(!configured || !state.fileValid || busy || unresolved || state.job);
    $('analyze-button').querySelector('span').textContent = state.submitting ? 'Enviando vídeo…' : 'Analizar vídeo';
    $('save-review').disabled = state.reviewSaving || !state.job?.result;
    $('new-analysis').disabled = state.reviewSaving || state.submitting;
    $('review-form').querySelectorAll('textarea, select').forEach(input => { input.disabled = state.reviewSaving; });
    ['export-json', 'export-html'].forEach(id => {
      const link = $(id);
      const blocked = state.reviewSaving || state.reviewDirty;
      link.setAttribute('aria-disabled', String(blocked));
      link.classList.toggle('is-disabled', blocked);
    });
    $('export-help').textContent = state.reviewDirty ? 'Guarda tu revisión para incluirla en ambos informes.' : 'Incluye fuente, incertidumbres y revisión guardada. El vídeo se queda fuera.';
    $('submit-help').textContent = !state.config ? 'Esperando conexión con el laboratorio.'
      : state.config.mode === 'azure' && !state.config.cloud_enabled ? 'Despliega y configura los recursos Azure para poder analizar vídeos.'
      : state.pendingSubmission ? 'El envío aún no está confirmado. Recupera la misma solicitud antes de iniciar otra.'
      : state.job?.status === 'uncertain' ? 'Esta operación requiere consulta. No se creará otro análisis automáticamente.'
      : 'El análisis revisa una grabación; no certifica la ejecución de un procedimiento.';
    const count = $('steps-list').children.length;
    const frozenSchema = state.config?.editable_steps === false;
    $('step-count').textContent = `${count} / 6`;
    $('add-step').hidden = frozenSchema;
    $('add-step').disabled = count >= 6 || Boolean(busy || state.job || unresolved);
    document.querySelectorAll('.step-id, .step-label').forEach(input => { input.readOnly = frozenSchema; });
    document.querySelectorAll('.remove-step').forEach(button => { button.hidden = frozenSchema; button.disabled = count <= 3 || Boolean(busy || state.job || unresolved); });
  }
  function addStep(step, focus = false) {
    if ($('steps-list').children.length >= 6) return;
    const index = $('steps-list').children.length;
    const row = element('li', 'step-editor');
    row.append(element('span', 'step-number', String(index + 1).padStart(2, '0')));
    const id = element('input', 'step-id');
    id.value = step.id; id.maxLength = 32; id.required = true;
    id.setAttribute('aria-label', `Identificador del paso ${index + 1}`);
    id.autocomplete = 'off'; id.spellcheck = false;
    const wrap = element('div', 'step-label-wrap');
    const input = element('textarea', 'step-label');
    input.rows = 2;
    input.id = `step-label-${index}`; input.value = step.label; input.maxLength = 240; input.required = true;
    input.placeholder = 'Una acción visible'; input.setAttribute('aria-label', `Descripción del paso ${index + 1}`);
    wrap.append(input);
    const remove = element('button', 'remove-step');
    remove.type = 'button'; remove.setAttribute('aria-label', `Eliminar paso ${index + 1}`);
    remove.innerHTML = '<svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15"/></svg>';
    remove.addEventListener('click', () => { row.remove(); renumberSteps(); invalidateRequest(); updateControls(); });
    [id, input].forEach(field => field.addEventListener('input', () => { field.classList.remove('invalid'); invalidateRequest(); }));
    row.append(id, wrap, remove); $('steps-list').append(row); updateControls();
    if (focus) input.focus();
  }
  function renumberSteps() {
    [...$('steps-list').children].forEach((row, index) => {
      row.querySelector('.step-number').textContent = String(index + 1).padStart(2, '0');
      row.querySelector('.step-id').setAttribute('aria-label', `Identificador del paso ${index + 1}`);
      row.querySelector('.step-label').id = `step-label-${index}`;
      row.querySelector('.step-label').setAttribute('aria-label', `Descripción del paso ${index + 1}`);
      row.querySelector('.remove-step').setAttribute('aria-label', `Eliminar paso ${index + 1}`);
    });
  }
  function setSteps(steps) {
    $('steps-list').replaceChildren();
    (Array.isArray(steps) && steps.length >= 3 && steps.length <= 6 ? steps : defaults).forEach(step => addStep(step));
  }
  function getSteps() {
    const steps = [...$('steps-list').children].map(row => ({ id: row.querySelector('.step-id').value.trim(), label: row.querySelector('.step-label').value.trim() }));
    if (steps.length < 3 || steps.length > 6) throw new Error('La checklist debe tener entre 3 y 6 pasos.');
    const ids = new Set();
    for (let index = 0; index < steps.length; index++) {
      const row = $('steps-list').children[index];
      if (!/^[a-zA-Z0-9_-]{1,32}$/.test(steps[index].id) || ids.has(steps[index].id)) {
        row.querySelector('.step-id').classList.add('invalid'); row.querySelector('.step-id').focus();
        throw new Error('Cada paso necesita un ID único de hasta 32 caracteres: letras, números, guion o guion bajo.');
      }
      if (steps[index].label.length < 3) { row.querySelector('.step-label').classList.add('invalid'); row.querySelector('.step-label').focus(); throw new Error(`Describe la acción visible del paso ${index + 1} con al menos 3 caracteres.`); }
      ids.add(steps[index].id);
    }
    return steps;
  }
  function releasePreview() {
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = null;
  }
  async function selectFile(file) {
    if (!file || state.submitting || state.pendingSubmission || state.job) return;
    clearNotice(); invalidateRequest();
    const version = ++state.selectedVersion;
    state.file = file; state.fileValid = false; state.duration = null;
    releasePreview();
    const player = $('video-player'); player.pause(); player.removeAttribute('src'); player.load();
    player.hidden = true; $('video-empty').hidden = false;
    $('file-title').textContent = file.name;
    $('file-meta').textContent = `${(file.size / (1024 * 1024)).toFixed(2)} MiB · comprobando duración`;
    $('drop-zone').classList.add('has-file');
    $('file-validation').className = 'field-help';
    $('file-validation').textContent = 'Comprobando el archivo…';
    $('clip-name').textContent = file.name; $('clip-duration').textContent = '—';
    updateControls();
    const maxSize = Number(state.config?.max_size_mib) || 25;
    const maxDuration = Number(state.config?.max_duration_s) || 90;
    const fileError = !/\.mp4$/i.test(file.name) || file.type && file.type !== 'video/mp4' && file.type !== 'application/octet-stream'
      ? 'Elige un MP4 con vídeo H.264. Convierte los archivos de otro formato antes de subirlos.'
      : file.size === 0 ? 'Este archivo está vacío. Elige un vídeo MP4 válido.'
      : file.size > maxSize * 1024 * 1024 ? `El vídeo supera ${maxSize} MiB. Recórtalo o comprímelo antes de analizarlo.` : null;
    if (fileError) { fileInvalid(fileError); return; }
    state.previewUrl = URL.createObjectURL(file);
    try {
      const duration = await new Promise((resolve, reject) => {
        const timeout = setTimeout(() => finish(null, new Error('No se pudo leer el vídeo. Comprueba que es un MP4 H.264 reproducible.')), 15000);
        const finish = (value, error) => { clearTimeout(timeout); player.removeEventListener('loadedmetadata', loaded); player.removeEventListener('error', failed); error ? reject(error) : resolve(value); };
        const loaded = () => finish(player.duration);
        const failed = () => finish(null, new Error('El archivo no se puede reproducir. Comprueba que no está corrupto y usa MP4 H.264.'));
        player.addEventListener('loadedmetadata', loaded, { once: true });
        player.addEventListener('error', failed, { once: true });
        player.src = state.previewUrl; player.load();
      });
      if (version !== state.selectedVersion) return;
      if (!Number.isFinite(duration) || duration <= 0) throw new Error('No se pudo comprobar la duración del vídeo. Usa un MP4 H.264 válido.');
      state.duration = duration;
      player.hidden = false; $('video-empty').hidden = true;
      $('clip-duration').textContent = formatTime(duration * 1000);
      $('file-meta').textContent = `${(file.size / (1024 * 1024)).toFixed(2)} MiB · ${duration.toFixed(1)} segundos`;
      if (duration > maxDuration) throw new Error(`El vídeo dura ${duration.toFixed(1)} segundos. Recórtalo a ${maxDuration} segundos o menos.`);
      state.fileValid = true;
      $('file-validation').textContent = 'Previsualización lista. El servidor comprobará formato y contenido antes del análisis.';
      updateControls();
    } catch (error) { if (version === state.selectedVersion) fileInvalid(error.message); }
  }
  function fileInvalid(message) {
    state.fileValid = false;
    $('file-validation').className = 'field-help field-error'; $('file-validation').textContent = message;
    $('file-meta').textContent = `${(state.file.size / (1024 * 1024)).toFixed(2)} MiB · archivo no válido`;
    notify(`${message} No se ha enviado a analizar.`); updateControls();
  }
  async function loadSample() {
    if (state.loadingSample) return;
    state.loadingSample = true; updateControls(); clearNotice();
    $('sample-button').textContent = 'Cargando ejemplo…';
    try {
      const response = await fetch('/api/sample', { credentials: 'same-origin' });
      if (!response.ok) { let data; try { data = await response.json(); } catch { /* HTTP fallback. */ } throw new Error(data?.detail || 'El ejemplo no está disponible. Elige un MP4 propio para continuar.'); }
      const blob = await response.blob();
      const file = new File([blob], 'procedimiento-ejemplo.mp4', { type: 'video/mp4' });
      setSteps(state.config.example_steps);
      $('license').value = state.config.example_license || state.config.sample_license || 'Slashme · Tying off onto the middle of a rope · CC BY-SA 4.0. Fuente: https://commons.wikimedia.org/wiki/File:Tying_off_onto_the_middle_of_a_rope.webm · Licencia: https://creativecommons.org/licenses/by-sa/4.0/ · fragmento convertido para este laboratorio.';
      await selectFile(file);
    } catch (error) { notify(error.message); }
    finally { state.loadingSample = false; $('sample-button').textContent = 'Cargar vídeo de ejemplo'; updateControls(); }
  }
  function stopPolling() { clearTimeout(state.pollTimer); state.pollTimer = null; }
  function renderJob(job) {
    state.job = job; state.pendingSubmission = false; storedWrite(PENDING_KEY, null);
    if (job.id) storedWrite(STORAGE_KEY, job.id);
    $('job-section').hidden = false;
    $('job-section').className = `job-section${ACTIVE.has(job.status) ? ' is-active' : job.status === 'failed' ? ' is-failed' : job.status === 'uncertain' ? ' is-uncertain' : ''}`;
    $('job-title').textContent = job.status === 'succeeded' ? 'Análisis terminado' : job.status === 'failed' ? 'El análisis no se completó' : job.status === 'uncertain' ? 'Operación sin resultado confirmado' : 'Análisis en curso';
    $('job-message').textContent = job.message || job.phase || 'Consultando la operación guardada.';
    $('job-reference').textContent = job.id ? `Operación ${job.id}` : '';
    const index = { queued: 0, validating: 0, submitting: 1, processing: 2, succeeded: 4 }[job.status] ?? -1;
    [...$('job-phases').children].forEach((item, position) => { item.className = position < index ? 'done' : position === index ? 'current' : ''; });
    $('check-job').hidden = !['uncertain', 'failed'].includes(job.status);
    $('check-job').textContent = job.can_resume ? 'Consultar operación' : 'Consultar estado';
    $('new-analysis').hidden = job.status !== 'succeeded' && job.status !== 'failed';
    if (job.result && state.renderedResultId !== job.id) renderResult(job);
    if (!state.file && job.id) {
      const clip = job.result?.clip;
      if (clip) {
        $('file-title').textContent = clip.name || 'Vídeo de la operación recuperada';
        $('file-meta').textContent = typeof clip.duration_ms === 'number'
          ? `${(clip.duration_ms / 1000).toFixed(1)} segundos · operación recuperada`
          : 'Vídeo de la operación recuperada';
        $('license').value = clip.license || '';
        $('drop-zone').classList.add('has-file');
      }
      const player = $('video-player');
      const path = `/api/jobs/${encodeURIComponent(job.id)}/video`;
      if (!player.getAttribute('src')?.endsWith(path)) { player.src = path; player.load(); }
      player.hidden = false; $('video-empty').hidden = true;
      $('clip-name').textContent = job.result?.clip?.name || 'Vídeo de la operación recuperada';
      $('clip-duration').textContent = job.result?.clip?.duration_ms ? formatTime(job.result.clip.duration_ms) : '—';
    }
    updateControls();
    stopPolling();
    if (ACTIVE.has(job.status)) state.pollTimer = setTimeout(() => pollJob(), 2000);
  }
  async function pollJob(manual = false) {
    if (state.polling || !state.job?.id) return;
    state.polling = true;
    $('check-job').disabled = true;
    const jobId = state.job.id;
    try {
      const job = await request(`/api/jobs/${encodeURIComponent(jobId)}`);
      if (state.job?.id !== jobId) return;
      renderJob(job);
      if (manual) clearNotice();
    } catch (error) {
      if (state.job?.id !== jobId) return;
      stopPolling(); $('check-job').hidden = false;
      $('check-job').textContent = 'Consultar estado';
      notify('No se pudo consultar el estado. La operación sigue guardada; usa «Consultar estado» para recuperarla. No se ha enviado otro análisis.', 'warning');
    } finally { state.polling = false; $('check-job').disabled = false; }
  }
  async function checkJob() {
    if (state.pendingSubmission) {
      $('check-job').disabled = true;
      try { renderJob(await request(`/api/jobs/by-key/${encodeURIComponent(state.requestKey)}`)); clearNotice(); }
      catch (error) {
        if (error.status === 404 && state.fileValid) await submitAnalysis(true);
        else if (error.status === 404) {
          state.pendingSubmission = false; state.requestKey = null; storedWrite(PENDING_KEY, null);
          $('job-section').hidden = true;
          notify('El servidor no tiene ninguna operación registrada para ese envío. Elige el vídeo y prepara una nueva solicitud explícita.', 'info');
          updateControls();
        } else notify('Aún no se pudo confirmar el envío. Conserva esta página y vuelve a consultar; no se ha creado otro análisis.', 'warning');
      } finally { $('check-job').disabled = false; }
      return;
    }
    if (state.job?.can_resume) {
      $('check-job').disabled = true;
      try { renderJob(await request(`/api/jobs/${encodeURIComponent(state.job.id)}/resume`, { method: 'POST' })); clearNotice(); }
      catch (error) { notify(error.message, 'warning'); }
      finally { $('check-job').disabled = false; }
    } else await pollJob(true);
  }
  async function submitAnalysis(recover = false) {
    if (state.submitting || !state.fileValid || state.job || state.pendingSubmission && !recover) return;
    if (!state.config?.cloud_enabled) return;
    let steps;
    try { steps = getSteps(); } catch (error) { notify(error.message); return; }
    const license = $('license').value.trim();
    if (!license) { $('license').classList.add('invalid'); $('license').focus(); notify('Indica la fuente y licencia, o el permiso de tu grabación propia, antes de continuar.'); return; }
    state.submitting = true; clearNotice();
    state.requestKey ||= crypto.randomUUID();
    storedWrite(PENDING_KEY, state.requestKey);
    updateControls();
    $('job-section').hidden = false; $('job-section').className = 'job-section is-active';
    [...$('job-phases').children].forEach((item, index) => { item.className = index === 0 ? 'current' : ''; });
    $('job-title').textContent = recover ? 'Recuperando el envío' : 'Enviando el vídeo';
    $('job-message').textContent = recover ? 'Consultando la misma solicitud, sin crear otra operación.' : 'El servidor comprobará el archivo antes de analizarlo.';
    $('job-reference').textContent = ''; $('check-job').hidden = true; $('new-analysis').hidden = true;
    const form = new FormData(); form.append('video', state.file); form.append('steps', JSON.stringify(steps)); form.append('license', license);
    try {
      const job = await request('/api/jobs', { method: 'POST', headers: { 'Idempotency-Key': state.requestKey }, body: form });
      renderJob(job);
      if (job.status === 'failed') notify(job.message || 'Revisa el archivo y los datos antes de preparar otro análisis.');
    } catch (error) {
      const definitive = error.status >= 400 && error.status < 500;
      if (definitive) {
        state.pendingSubmission = false; storedWrite(PENDING_KEY, null);
        $('job-section').hidden = true;
        notify(error.message);
      } else {
        state.pendingSubmission = true;
        $('job-section').className = 'job-section is-uncertain';
        $('job-title').textContent = 'Envío sin confirmación';
        $('job-message').textContent = 'La conexión se interrumpió antes de recibir el identificador. Recupera esta misma solicitud.';
        $('check-job').hidden = false; $('check-job').textContent = 'Recuperar el mismo envío';
        notify('No se pudo confirmar el envío. «Recuperar el mismo envío» reutiliza su identificador y no solicita un segundo análisis.', 'warning');
      }
    } finally { state.submitting = false; updateControls(); }
  }
  function eventSource(event) {
    const source = String(event.time_source || '').toLowerCase();
    if ((source.includes('estimat') || source.includes('model')) && state.job?.result?.analysis_provider === 'azure_foundry_responses') return 'Tiempo estimado sobre fotogramas · requiere revisión';
    if (source.includes('estimat') || source.includes('model')) return 'Tiempo estimado por el modelo · requiere revisión';
    if (source.includes('service') || source.includes('segment') || source.includes('azure')) return 'Tiempo del segmento devuelto por el servicio';
    return 'Origen temporal no confirmado';
  }
  function providerName(result, job) {
    if (result.analysis_provider === 'azure_foundry_responses') return 'Microsoft Foundry · Azure Responses';
    if (!result.analysis_provider || result.analysis_provider === 'azure_content_understanding') return 'Azure Content Understanding';
    return stringify(result.analysis_provider);
  }
  function samplingSummary(sampling) {
    if (!sampling || typeof sampling !== 'object') return null;
    const parts = [];
    if (Number.isFinite(sampling.fps) && sampling.fps > 0) parts.push(`${sampling.fps} fotogramas/s`);
    if (Number.isInteger(sampling.actual_frame_count) && sampling.actual_frame_count >= 0) parts.push(`${sampling.actual_frame_count} fotogramas analizados`);
    if (Number.isFinite(sampling.max_edge_px) && sampling.max_edge_px > 0) parts.push(`lado máximo ${sampling.max_edge_px} px`);
    return parts.length ? parts.join(' · ') : 'Datos de muestreo no disponibles';
  }
  function seekEvent(event, row, durationMs) {
    if (!validTime(event, durationMs)) return;
    const player = $('video-player');
    if (!player.getAttribute('src')) { notify('El vídeo ya no está disponible para navegar. Los informes conservan la evidencia.', 'warning'); return; }
    const seek = () => {
      if (!Number.isFinite(player.duration) || event.start_ms / 1000 >= player.duration) { notify('Este intervalo no está disponible en el vídeo actual. Revisa la duración del archivo.', 'warning'); return; }
      player.currentTime = event.start_ms / 1000;
      document.querySelectorAll('.event.active').forEach(node => node.classList.remove('active'));
      row.classList.add('active');
      $('active-evidence').hidden = false;
      $('active-evidence').textContent = `${formatTime(event.start_ms, true)}–${formatTime(event.end_ms, true)} · ${event.action || 'Acción observada'} · ${eventSource(event)}`;
      if (matchMedia('(max-width: 670px)').matches) $('viewer-title').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
    };
    if (player.readyState >= 1) seek(); else player.addEventListener('loadedmetadata', seek, { once: true });
  }
  function renderEvent(event, durationMs) {
    const row = element('div', 'event');
    const valid = validTime(event, durationMs);
    const button = element('button', 'event-seek', valid ? `${formatTime(event.start_ms, true)} – ${formatTime(event.end_ms, true)}` : 'Sin tiempo válido');
    button.type = 'button'; button.disabled = !valid;
    button.setAttribute('aria-label', valid ? `Ir a ${formatTime(event.start_ms, true)}: ${event.action || 'evidencia visual'}` : 'Este evento no tiene un intervalo válido para navegar');
    button.addEventListener('click', () => seekEvent(event, row, durationMs));
    const copy = element('div', 'event-copy');
    copy.append(element('p', '', event.action || 'Acción sin descripción'));
    if (Array.isArray(event.objects) && event.objects.length) copy.append(element('span', '', `Objetos: ${event.objects.map(stringify).join(', ')}`));
    copy.append(element('span', eventSource(event).includes('estimado') ? 'event-tag' : '', valid ? eventSource(event) : 'Intervalo ausente o inválido · no permite salto'));
    if (event.evidence_status === 'uncertain') copy.append(element('span', 'event-tag', 'Evidencia incierta'));
    row.append(button, copy); return row;
  }
  function renderResult(job) {
    const result = job.result; const checklist = Array.isArray(result.checklist) ? result.checklist : [];
    const events = Array.isArray(result.events) ? result.events : [];
    const durationMs = result.clip?.duration_ms;
    state.renderedResultId = job.id;
    $('result-empty').hidden = true; $('result-content').hidden = false;
    $('result-source').className = 'result-source';
    $('result-source').textContent = `${providerName(result, job)} · pendiente de revisión humana.`;
    $('order-notice').hidden = !result.order_changed;
    $('order-notice').textContent = 'El orden visible difiere del esperado. Comprueba los intervalos: esta diferencia describe la grabación y no demuestra un incumplimiento.';
    $('checklist').replaceChildren();
    const used = new Set();
    checklist.forEach(item => {
      const row = element('li', 'checklist-item');
      const head = element('div', 'checklist-head');
      const status = labels[item.status] ? item.status : 'uncertain';
      head.append(element('h3', 'checklist-label', item.label || item.id), element('span', `checklist-status ${status}`, labels[status]));
      row.append(head);
      let linked = Array.isArray(item.events) ? item.events.map(entry => typeof entry === 'object' && entry !== null ? entry : events.find(event => event.id === entry)).filter(Boolean) : [];
      if (!linked.length) linked = events.filter(event => event.step_id === item.id);
      linked.forEach(event => { if (event.id) used.add(event.id); row.append(renderEvent(event, durationMs)); });
      if (!linked.length) row.append(element('p', 'checklist-empty', status === 'not_observed' ? 'No hay evidencia de este paso en el vídeo.' : 'No hay evidencia temporal suficiente para confirmar este paso.'));
      $('checklist').append(row);
    });
    const unmatched = events.filter(event => !used.has(event.id) && !checklist.some(item => item.id === event.step_id));
    $('unmatched-events').hidden = !unmatched.length;
    $('unmatched-list').replaceChildren(...unmatched.map(event => renderEvent(event, durationMs)));
    $('result-issues').replaceChildren();
    const issues = Array.isArray(result.issues) ? result.issues : [];
    $('result-issues').hidden = !issues.length;
    if (issues.length) $('result-issues').append(element('p', '', 'Hay observaciones que no se pudieron confirmar. Revisa las filas inciertas y sus evidencias antes de decidir.'));
    const metadata = [
      ['Archivo', result.clip?.name], ['SHA-256', result.clip?.hash], ['Duración', formatTime(durationMs)],
      ['Esquema', result.schema_version], ['API', result.api_version], ['Modelo', result.model],
      ['Versión del modelo', result.model_version],
      ['Origen', 'Azure'],
      ['Proveedor', providerName(result, job)],
      ['Fuente y licencia', result.clip?.license],
    ];
    const sampling = samplingSummary(result.frame_sampling);
    if (sampling) metadata.push(['Muestreo visual', sampling], ['Tiempos', 'Estimaciones del modelo sobre fotogramas; requieren revisión.']);
    $('analysis-metadata').replaceChildren();
    metadata.forEach(([label, value]) => $('analysis-metadata').append(element('dt', '', label), element('dd', '', stringify(value))));
    renderReview(result);
    $('export-json').href = `/api/jobs/${encodeURIComponent(job.id)}/export.json`;
    $('export-html').href = `/api/jobs/${encodeURIComponent(job.id)}/export.html`;
    if (!state.file && result.clip?.name) $('clip-name').textContent = result.clip.name;
    if (typeof durationMs === 'number') $('clip-duration').textContent = formatTime(durationMs);
  }
  function renderReview(result) {
    $('review-decisions').replaceChildren();
    (result.checklist || []).forEach((item, index) => {
      const row = element('div', 'review-decision');
      const label = element('label', '', item.label || item.id);
      label.htmlFor = `review-${index}`;
      const select = element('select'); select.id = `review-${index}`; select.dataset.stepId = item.id;
      const options = [['', 'Sin decisión humana'], ['observed', 'Confirmo: observado'], ['not_observed', 'Confirmo: no observado'], ['uncertain', 'Mantengo la duda']];
      options.forEach(([value, text]) => { const option = element('option', '', text); option.value = value; select.append(option); });
      select.value = result.review?.decisions?.find(decision => decision.step_id === item.id)?.status || '';
      select.addEventListener('change', markReviewDirty);
      row.append(label, select); $('review-decisions').append(row);
    });
    $('review-notes').value = result.review?.notes || '';
    state.reviewDirty = false;
    $('review-status').textContent = result.review?.updated_at ? 'Revisión guardada' : '';
    updateControls();
  }
  function markReviewDirty() {
    state.reviewDirty = true;
    $('review-status').textContent = 'Cambios sin guardar';
    updateControls();
  }
  async function saveReview(event) {
    event.preventDefault(); if (state.reviewSaving || !state.job?.result) return;
    state.reviewSaving = true; updateControls(); $('review-status').textContent = 'Guardando…';
    const review = { notes: $('review-notes').value, decisions: [...$('review-decisions').querySelectorAll('select')].filter(select => select.value).map(select => ({ step_id: select.dataset.stepId, status: select.value })) };
    try {
      const response = await request(`/api/jobs/${encodeURIComponent(state.job.id)}/review`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(review) });
      state.job.result.review = response.review || response.result?.review || review;
      state.reviewDirty = false;
      $('review-status').textContent = 'Revisión guardada';
    } catch (error) { $('review-status').textContent = 'No se ha guardado'; notify(error.message); }
    finally { state.reviewSaving = false; updateControls(); }
  }
  function newAnalysis() {
    if (!state.job || !['succeeded', 'failed'].includes(state.job.status)) return;
    if (state.reviewDirty && !window.confirm('Tu revisión tiene cambios sin guardar. ¿Descartarlos y preparar otro análisis?')) return;
    stopPolling(); state.job = null; state.renderedResultId = null; state.requestKey = null;
    state.reviewDirty = false;
    storedWrite(STORAGE_KEY, null); storedWrite(PENDING_KEY, null);
    $('job-section').hidden = true; $('result-empty').hidden = false; $('result-content').hidden = true;
    $('active-evidence').hidden = true;
    clearNotice(); updateControls();
    if (state.config?.mode === 'azure') notify('Cada análisis llama al modelo desplegado en tu suscripción Azure y genera consumo.', 'info');
    if (!state.file) {
      releasePreview(); $('video-player').pause(); $('video-player').removeAttribute('src'); $('video-player').load();
      $('video-player').hidden = true; $('video-empty').hidden = false; $('clip-name').textContent = 'Sin vídeo seleccionado'; $('clip-duration').textContent = '—';
      $('file-title').textContent = 'Elige o arrastra un vídeo';
      $('file-meta').textContent = `MP4 · hasta ${state.config?.max_size_mib || 25} MiB · hasta ${state.config?.max_duration_s || 90} segundos`;
      $('file-validation').className = 'field-help'; $('file-validation').textContent = 'La validación completa se realiza antes de enviar el vídeo.';
      $('drop-zone').classList.remove('has-file'); $('license').value = ''; $('video-file').value = '';
    }
    $('setup-title').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' });
  }
  async function initialize() {
    setSteps(defaults);
    try {
      state.config = await request('/api/config');
      $('mode-banner').className = `mode-banner${!state.config.cloud_enabled ? ' offline' : ''}`;
      $('mode-title').textContent = state.config.cloud_enabled ? 'Análisis Azure disponible' : 'Azure sin configurar';
      $('mode-description').textContent = state.config.cloud_enabled ? 'El vídeo se analiza con el modelo desplegado en tu Azure. Cada envío genera consumo.' : 'Despliega la aplicación y sus recursos con azd para analizar vídeos reales.';
      $('setup-description').textContent = 'Un vídeo. Entre 3 y 6 acciones visibles.';
      $('steps-help').textContent = 'Describe las acciones que quieres comprobar, en el orden esperado. El modelo debe aportar evidencia visual para cada una.';
      const maxSize = Number(state.config.max_size_mib) || 25, maxDuration = Number(state.config.max_duration_s) || 90;
      $('file-meta').textContent = `MP4 · hasta ${maxSize} MiB · hasta ${maxDuration} segundos`;
      $('sample-button').hidden = !state.config.sample_available;
      setSteps(state.config.example_steps);
      const warnings = Array.isArray(state.config.warnings) ? state.config.warnings : [];
      $('config-warnings').hidden = !warnings.length;
      $('config-warnings').replaceChildren(...warnings.map(warning => element('p', '', warning)));
      const jobId = storedRead(STORAGE_KEY);
      if (jobId) {
        state.recovering = true; updateControls();
        try { renderJob(await request(`/api/jobs/${encodeURIComponent(jobId)}`)); }
        catch (error) {
          if (error.status === 404) { storedWrite(STORAGE_KEY, null); notify('La operación guardada ya no está disponible. Elige un vídeo para iniciar una nueva revisión.', 'info'); }
          else {
            state.job = { id: jobId, status: 'uncertain', message: 'No se pudo recuperar la operación. Consulta su estado antes de iniciar otra.' };
            renderJob(state.job); notify('No se pudo recuperar la operación guardada. No se enviará otro análisis hasta consultar su estado.', 'warning');
          }
        } finally { state.recovering = false; }
      } else if (storedRead(PENDING_KEY)) {
        state.requestKey = storedRead(PENDING_KEY);
        state.pendingSubmission = true; state.recovering = true; updateControls();
        $('job-section').hidden = false; $('job-section').className = 'job-section is-uncertain';
        $('job-title').textContent = 'Recuperando el envío anterior';
        $('job-message').textContent = 'Consultando su identificador guardado, sin volver a enviar el vídeo.';
        $('check-job').hidden = false; $('check-job').textContent = 'Consultar envío';
        try {
          renderJob(await request(`/api/jobs/by-key/${encodeURIComponent(state.requestKey)}`));
        } catch (error) {
          if (error.status === 404) {
            state.pendingSubmission = false; state.requestKey = null; storedWrite(PENDING_KEY, null); $('job-section').hidden = true;
            notify('El servidor no tiene una operación registrada para el envío anterior. Puedes elegir un vídeo y preparar una nueva revisión.', 'info');
          } else notify('La sesión anterior terminó antes de confirmar el envío. Usa «Consultar envío» para recuperar el estado; no se enviará otro análisis.', 'warning');
        } finally { state.recovering = false; }
      }
      updateControls();
    } catch (error) {
      $('mode-banner').className = 'mode-banner offline';
      $('mode-title').textContent = 'El laboratorio no está disponible';
      $('mode-description').textContent = 'Comprueba que el servidor está iniciado y recarga esta página.';
      notify(error.message); updateControls();
    }
  }

  $('video-file').addEventListener('change', event => selectFile(event.target.files[0]));
  const dropZone = $('drop-zone');
  ['dragenter', 'dragover'].forEach(type => dropZone.addEventListener(type, event => { event.preventDefault(); if (!$('input-fields').disabled) dropZone.classList.add('dragover'); }));
  ['dragleave', 'drop'].forEach(type => dropZone.addEventListener(type, event => { event.preventDefault(); dropZone.classList.remove('dragover'); }));
  dropZone.addEventListener('drop', event => { if (!$('input-fields').disabled) selectFile(event.dataTransfer.files[0]); });
  $('sample-button').addEventListener('click', loadSample);
  $('add-step').addEventListener('click', () => { const existing = [...$('steps-list').querySelectorAll('.step-id')].map(input => input.value); let number = $('steps-list').children.length + 1; while (existing.includes(`paso_${number}`)) number++; addStep({ id: `paso_${number}`, label: '' }, true); invalidateRequest(); });
  $('license').addEventListener('input', () => { $('license').classList.remove('invalid'); invalidateRequest(); });
  $('analysis-form').addEventListener('submit', event => { event.preventDefault(); submitAnalysis(); });
  $('check-job').addEventListener('click', checkJob);
  $('new-analysis').addEventListener('click', newAnalysis);
  $('review-form').addEventListener('submit', saveReview);
  $('review-notes').addEventListener('input', markReviewDirty);
  ['export-json', 'export-html'].forEach(id => $(id).addEventListener('click', event => {
    if (state.reviewSaving || state.reviewDirty) {
      event.preventDefault(); notify('Guarda la revisión humana antes de descargar el informe con tus cambios.', 'info');
    }
  }));
  window.addEventListener('beforeunload', event => {
    if (state.reviewDirty) { event.preventDefault(); event.returnValue = ''; }
  });
  $('video-player').addEventListener('error', () => { if (state.job && !state.file) notify('El vídeo de esta operación no está disponible. Puedes conservar la checklist y exportar los informes.', 'warning'); });
  window.addEventListener('pagehide', stopPolling);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && state.job && ACTIVE.has(state.job.status)) pollJob(); });
  initialize();
})();
