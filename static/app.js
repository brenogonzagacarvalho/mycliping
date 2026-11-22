// ClipStudio AI - Frontend Controller

document.addEventListener("DOMContentLoaded", () => {
  // -------------------------------------------------------------------------
  // State
  // -------------------------------------------------------------------------
  let currentTab = "tab-videos";
  let activeLiveSession = null;
  let livePollInterval = null;
  let activeTaskId = null;
  let taskPollInterval = null;
  let socket = null;
  let selectedUploadFile = null;

  // -------------------------------------------------------------------------
  // Elements
  // -------------------------------------------------------------------------
  const navItems = document.querySelectorAll(".nav-item");
  const tabPanes = document.querySelectorAll(".tab-pane");
  const currentTabTitle = document.getElementById("current-tab-title");
  const galleryBadge = document.getElementById("gallery-badge");

  // Input Type Switcher (URL vs File)
  const btnInputUrl = document.getElementById("btn-input-url");
  const btnInputFile = document.getElementById("btn-input-file");
  const sectionUrl = document.getElementById("section-url");
  const sectionFile = document.getElementById("section-file");

  // Video Process Inputs
  const videoUrlInput = document.getElementById("video-url-input");
  const btnInspectUrl = document.getElementById("btn-inspect-url");
  const urlPreviewCard = document.getElementById("url-preview-card");
  const previewThumb = document.getElementById("preview-thumb");
  const previewTitle = document.getElementById("preview-title");
  const previewChannel = document.getElementById("preview-channel");
  const previewDuration = document.getElementById("preview-duration");

  const dropZone = document.getElementById("drop-zone");
  const videoFileInput = document.getElementById("video-file-input");
  const btnBrowseFile = document.getElementById("btn-browse-file");
  const selectedFileDisplay = document.getElementById("selected-file-display");

  const optCaptionStyle = document.getElementById("opt-caption-style");
  const optLayoutMode = document.getElementById("opt-layout-mode");
  const optMaxClips = document.getElementById("opt-max-clips");
  const optDurationRange = document.getElementById("opt-duration-range");
  const btnStartProcess = document.getElementById("btn-start-process");

  // Live Stream Elements
  const liveUrlInput = document.getElementById("live-url-input");
  const btnConnectLive = document.getElementById("btn-connect-live");
  const liveStation = document.getElementById("live-station");
  const liveStreamTitle = document.getElementById("live-stream-title");
  const liveStreamChannel = document.getElementById("live-stream-channel");
  const btnStopLive = document.getElementById("btn-stop-live");
  const liveBufferTime = document.getElementById("live-buffer-time");
  const bufferBarFill = document.getElementById("buffer-bar-fill");
  const quickClipButtons = document.querySelectorAll(".btn-quick-clip");

  // Gallery
  const clipsGrid = document.getElementById("clips-grid");
  const galleryEmptyState = document.getElementById("gallery-empty-state");
  const btnRefreshGallery = document.getElementById("btn-refresh-gallery");
  const btnSelectAllClips = document.getElementById("btn-select-all-clips");
  const btnPostSelected = document.getElementById("btn-post-selected");
  const selectedClipsCount = document.getElementById("selected-clips-count");
  const selectedClipIds = new Set();
  let galleryClipsCache = [];

  // Settings
  const settingsForm = document.getElementById("settings-form");
  const setAiProvider = document.getElementById("set-ai-provider");
  const fieldsOllama = document.getElementById("fields-ollama");
  const fieldsGemini = document.getElementById("fields-gemini");
  const fieldsOpenai = document.getElementById("fields-openai");
  const fieldsGroq = document.getElementById("fields-groq");
  const setOllamaUrl = document.getElementById("set-ollama-url");
  const setOllamaModel = document.getElementById("set-ollama-model");
  const setGeminiKey = document.getElementById("set-gemini-key");
  const setGeminiModel = document.getElementById("set-gemini-model");
  const setOpenaiKey = document.getElementById("set-openai-key");
  const setOpenaiModel = document.getElementById("set-openai-model");
  const setGroqKey = document.getElementById("set-groq-key");
  const setGroqModel = document.getElementById("set-groq-model");
  const setWhisperModel = document.getElementById("set-whisper-model");
  const setWhisperLang = document.getElementById("set-whisper-lang");
  const setMaxJobs = document.getElementById("set-max-jobs");
  const setVideoEncoder = document.getElementById("set-video-encoder");
  const setCleanupSource = document.getElementById("set-cleanup-source");
  const hwInfo = document.getElementById("hw-info");
  const settingsSaveMsg = document.getElementById("settings-save-msg");
  const currentAiMode = document.getElementById("current-ai-mode");

  // Modals
  const progressModal = document.getElementById("progress-modal");
  const modalPct = document.getElementById("modal-pct");
  const modalTitle = document.getElementById("modal-title");
  const modalBarFill = document.getElementById("modal-bar-fill");
  const modalStage = document.getElementById("modal-stage");
  const modalMsg = document.getElementById("modal-msg");
  const modalFooter = document.getElementById("modal-footer");
  const btnModalViewClips = document.getElementById("btn-modal-view-clips");

  const videoModal = document.getElementById("video-modal");
  const activeVideoPlayer = document.getElementById("active-video-player");
  const videoModalTitle = document.getElementById("video-modal-title");
  const videoModalScores = document.getElementById("video-modal-scores");
  const btnCloseVideoModal = document.getElementById("btn-close-video-modal");
  const btnDownloadActiveVideo = document.getElementById("btn-download-active-video");
  const btnCopyHashtags = document.getElementById("btn-copy-hashtags");

  // -------------------------------------------------------------------------
  // WebSocket Connection
  // -------------------------------------------------------------------------
  function setupWebSocket() {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${proto}//${window.location.host}/ws/tasks`;
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      console.log("WebSocket connected to ClipStudio server.");
    };

    socket.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.task_id === activeTaskId) {
          updateProgressUI(data);
        }
      } catch (err) {
        console.error("WS error:", err);
      }
    };

    socket.onclose = () => {
      setTimeout(setupWebSocket, 3000);
    };
  }

  setupWebSocket();

  // -------------------------------------------------------------------------
  // Navigation Tabs
  // -------------------------------------------------------------------------
  navItems.forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetTab = btn.getAttribute("data-tab");
      switchTab(targetTab);
    });
  });

  function switchTab(tabId) {
    currentTab = tabId;
    navItems.forEach((item) => {
      item.classList.toggle("active", item.getAttribute("data-tab") === tabId);
    });
    tabPanes.forEach((pane) => {
      pane.classList.toggle("active", pane.id === tabId);
    });

    const titles = {
      "tab-videos": "Clipar Vídeos",
      "tab-lives": "Clipar Lives Ao Vivo",
      "tab-gallery": "Galeria de Clipes",
      "tab-settings": "Configurações & IA"
    };
    currentTabTitle.textContent = titles[tabId] || "ClipStudio";

    if (tabId === "tab-gallery") {
      loadGallery();
    }
  }

  // -------------------------------------------------------------------------
  // Input Switcher (URL vs Local File)
  // -------------------------------------------------------------------------
  btnInputUrl.addEventListener("click", () => {
    btnInputUrl.classList.add("active");
    btnInputFile.classList.remove("active");
    sectionUrl.style.display = "block";
    sectionFile.style.display = "none";
  });

  btnInputFile.addEventListener("click", () => {
    btnInputFile.classList.add("active");
    btnInputUrl.classList.remove("active");
    sectionFile.style.display = "block";
    sectionUrl.style.display = "none";
  });

  // URL Inspect
  btnInspectUrl.addEventListener("click", async () => {
    const url = videoUrlInput.value.trim();
    if (!url) return;

    btnInspectUrl.textContent = "Verificando...";
    btnInspectUrl.disabled = true;

    try {
      const res = await fetch(`/api/info?url=${encodeURIComponent(url)}`);
      if (!res.ok) throw new Error("Erro ao consultar vídeo");
      const data = await res.json();

      urlPreviewCard.style.display = "flex";
      previewThumb.src = data.thumbnail || "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe";
      previewTitle.textContent = data.title;
      previewChannel.textContent = data.uploader || "Canal";
      
      const mins = Math.floor(data.duration / 60);
      const secs = Math.floor(data.duration % 60);
      previewDuration.textContent = `${mins}:${secs.toString().padStart(2, '0')}`;
    } catch (err) {
      alert("Não foi possível carregar a miniatura do vídeo. Certifique-se de que a URL é válida.");
    } finally {
      btnInspectUrl.textContent = "Verificar";
      btnInspectUrl.disabled = false;
    }
  });

  // File Upload Drag & Drop
  btnBrowseFile.addEventListener("click", () => videoFileInput.click());

  videoFileInput.addEventListener("change", (e) => {
    if (e.target.files && e.target.files[0]) {
      handleFileSelected(e.target.files[0]);
    }
  });

  dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
  });

  dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragover");
  });

  dropZone.addEventListener("drop", (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileSelected(e.dataTransfer.files[0]);
    }
  });

  function handleFileSelected(file) {
    selectedUploadFile = file;
    selectedFileDisplay.textContent = `Arquivo: ${file.name} (${(file.size / (1024 * 1024)).toFixed(1)} MB)`;
    selectedFileDisplay.style.display = "inline-block";
  }

  // -------------------------------------------------------------------------
  // Start Video Processing
  // -------------------------------------------------------------------------
  btnStartProcess.addEventListener("click", async () => {
    const isUrlMode = btnInputUrl.classList.contains("active");
    const [minDur, maxDur] = optDurationRange.value.split("-").map(Number);

    if (isUrlMode) {
      const url = videoUrlInput.value.trim();
      if (!url) {
        alert("Por favor, cole o link do vídeo para clipar.");
        return;
      }

      const payload = {
        url: url,
        max_clips: parseInt(optMaxClips.value),
        min_clip_duration: minDur,
        max_clip_duration: maxDur,
        caption_style: optCaptionStyle.value,
        layout_mode: optLayoutMode.value,
        aspect_ratio: "9:16"
      };

      try {
        btnStartProcess.disabled = true;
        const res = await fetch("/api/clips/process", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload)
        });

        if (!res.ok) throw new Error("Erro ao iniciar processamento");
        const data = await res.json();
        launchProgressModal(data.task_id, url);
      } catch (err) {
        alert(`Erro: ${err.message}`);
      } finally {
        btnStartProcess.disabled = false;
      }

    } else {
      // Upload mode
      if (!selectedUploadFile) {
        alert("Por favor, selecione ou arraste um vídeo do seu computador.");
        return;
      }

      const formData = new FormData();
      formData.append("file", selectedUploadFile);
      formData.append("max_clips", optMaxClips.value);
      formData.append("min_clip_duration", minDur);
      formData.append("max_clip_duration", maxDur);
      formData.append("caption_style", optCaptionStyle.value);
      formData.append("layout_mode", optLayoutMode.value);
      formData.append("aspect_ratio", "9:16");

      try {
        btnStartProcess.disabled = true;
        const res = await fetch("/api/upload", {
          method: "POST",
          body: formData
        });

        if (!res.ok) throw new Error("Erro ao enviar vídeo");
        const data = await res.json();
        launchProgressModal(data.task_id, selectedUploadFile.name);
      } catch (err) {
        alert(`Erro: ${err.message}`);
      } finally {
        btnStartProcess.disabled = false;
      }
    }
  });

  // -------------------------------------------------------------------------
  // Progress Modal & Polling
  // -------------------------------------------------------------------------
  function launchProgressModal(taskId, title) {
    activeTaskId = taskId;
    modalTitle.textContent = title;
    modalPct.textContent = "0%";
    modalBarFill.style.width = "0%";
    modalStage.textContent = "Iniciando";
    modalMsg.textContent = "Aguardando tarefas iniciais...";
    modalFooter.style.display = "none";
    resetStepVisualizer();

    progressModal.style.display = "flex";

    // Polling fallback in case WS lags
    if (taskPollInterval) clearInterval(taskPollInterval);
    taskPollInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/tasks/${taskId}`);
        if (res.ok) {
          const data = await res.json();
          updateProgressUI(data);
          if (data.status === "completed" || data.status === "error") {
            clearInterval(taskPollInterval);
          }
        }
      } catch (e) {}
    }, 2000);
  }

  function updateProgressUI(data) {
    const pct = Math.min(100, Math.max(0, data.progress || 0));
    modalPct.textContent = `${pct.toFixed(0)}%`;
    modalBarFill.style.width = `${pct}%`;
    modalStage.textContent = data.stage || "Processando";
    modalMsg.textContent = data.message || "";
    modalMsg.style.color = "";

    // Highlight step items
    const steps = [
      { id: "step-download", threshold: 10 },
      { id: "step-audio", threshold: 28 },
      { id: "step-transcribe", threshold: 32 },
      { id: "step-ai", threshold: 62 },
      { id: "step-render", threshold: 65 }
    ];

    steps.forEach((st, idx) => {
      const el = document.getElementById(st.id);
      if (!el) return;
      if (pct > st.threshold + 20 || data.status === "completed") {
        el.className = "step-item done";
      } else if (pct >= st.threshold) {
        el.className = "step-item active";
      } else {
        el.className = "step-item";
      }
    });

    if (data.status === "completed") {
      modalFooter.style.display = "flex";
      loadGallery();
    } else if (data.status === "error") {
      modalStage.textContent = "Erro!";
      modalMsg.textContent = data.message || data.error || "Ocorreu um erro no processamento.";
      modalMsg.style.color = "var(--accent-red)";
    }
  }

  function resetStepVisualizer() {
    ["step-download", "step-audio", "step-transcribe", "step-ai", "step-render"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.className = "step-item";
    });
  }

  btnModalViewClips.addEventListener("click", () => {
    progressModal.style.display = "none";
    switchTab("tab-gallery");
  });

  // -------------------------------------------------------------------------
  // Live Stream Hub
  // -------------------------------------------------------------------------
  btnConnectLive.addEventListener("click", async () => {
    const url = liveUrlInput.value.trim();
    if (!url) {
      alert("Informe a URL da live da Twitch, YouTube Live ou Kick.");
      return;
    }

    btnConnectLive.textContent = "Conectando...";
    btnConnectLive.disabled = true;

    try {
      const res = await fetch("/api/live/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || "Falha ao conectar na live");
      }

      const data = await res.json();
      activeLiveSession = data.session_id;

      liveStation.style.display = "block";
      liveStreamTitle.textContent = data.title;
      liveStreamChannel.textContent = data.channel;

      // Start polling buffer status
      startLivePolling(activeLiveSession);

    } catch (err) {
      alert(`Erro ao conectar: ${err.message}`);
    } finally {
      btnConnectLive.textContent = "Conectar na Live";
      btnConnectLive.disabled = false;
    }
  });

  function startLivePolling(sessionId) {
    if (livePollInterval) clearInterval(livePollInterval);

    livePollInterval = setInterval(async () => {
      try {
        const res = await fetch(`/api/live/status/${sessionId}`);
        if (res.ok) {
          const st = await res.json();
          const bufSec = st.buffered_seconds || 0;
          const mins = Math.floor(bufSec / 60);
          const secs = bufSec % 60;
          liveBufferTime.textContent = `${mins}m ${secs}s armazenados no buffer contínuo`;

          // Buffer bar (max 10 min = 600s)
          const fillPct = Math.min(100, (bufSec / 600) * 100);
          bufferBarFill.style.width = `${fillPct}%`;
        }
      } catch (e) {}
    }, 2000);
  }

  btnStopLive.addEventListener("click", async () => {
    if (!activeLiveSession) return;
    if (livePollInterval) clearInterval(livePollInterval);

    try {
      await fetch(`/api/live/stop?session_id=${activeLiveSession}`, { method: "POST" });
    } catch (e) {}

    activeLiveSession = null;
    liveStation.style.display = "none";
  });

  // Quick Clip buttons
  quickClipButtons.forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (!activeLiveSession) {
        alert("Nenhuma live ativa no momento.");
        return;
      }

      const seconds = parseInt(btn.getAttribute("data-seconds"));

      btn.style.opacity = "0.5";
      btn.style.pointerEvents = "none";

      try {
        const res = await fetch("/api/live/clip", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            session_id: activeLiveSession,
            seconds: seconds,
            caption_style: optCaptionStyle.value,
            layout_mode: optLayoutMode.value,
            aspect_ratio: "9:16"
          })
        });

        if (!res.ok) {
          const err = await res.json();
          throw new Error(err.detail || "Erro ao clipar");
        }

        const data = await res.json();
        launchProgressModal(data.task_id, `Corte da Live (${seconds}s)`);

      } catch (err) {
        alert(`Erro ao extrair clipe da live: ${err.message}`);
      } finally {
        btn.style.opacity = "1";
        btn.style.pointerEvents = "auto";
      }
    });
  });

  // -------------------------------------------------------------------------
  // Gallery Management
  // -------------------------------------------------------------------------
  async function loadGallery() {
    try {
      const res = await fetch("/api/gallery");
      if (!res.ok) return;
      const clips = await res.json();
      galleryClipsCache = clips;

      galleryBadge.textContent = clips.length;

      if (clips.length === 0) {
        galleryEmptyState.style.display = "block";
        // Remove old cards except empty state
        const oldCards = clipsGrid.querySelectorAll(".clip-card");
        oldCards.forEach(c => c.remove());
        return;
      }

      galleryEmptyState.style.display = "none";
      const oldCards = clipsGrid.querySelectorAll(".clip-card");
      oldCards.forEach(c => c.remove());

      clips.forEach((clip) => {
        const card = createClipCard(clip);
        clipsGrid.appendChild(card);
      });

    } catch (err) {
      console.error("Failed to load gallery:", err);
    }
  }

  function createClipCard(clip) {
    const card = document.createElement("div");
    card.className = "clip-card";

    const thumbUrl = `/api/clips/${clip.id}/thumb`;
    const videoUrl = `/api/clips/${clip.id}/video`;

    card.innerHTML = `
      <div class="clip-thumb-box" data-id="${clip.id}">
        <label class="clip-select-box" title="Selecionar para Auto-Postagem" style="position:absolute;top:10px;left:10px;z-index:5;background:rgba(0,0,0,0.65);padding:5px 8px;border-radius:8px;display:flex;align-items:center;gap:5px;cursor:pointer;font-size:12px;">
          <input type="checkbox" class="clip-select-cb" data-id="${clip.id}" ${selectedClipIds.has(clip.id) ? "checked" : ""}> Postar
        </label>
        <img src="${thumbUrl}" alt="${clip.hook_title}" onerror="this.src='https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe'">
        <div class="virality-score-badge">
          <span class="score-star">★</span>
          <span>${clip.virality_score}</span>
        </div>
        ${clip.is_live ? '<div class="live-clip-tag">🔴 LIVE</div>' : ''}
        <div class="clip-duration-badge">${clip.duration}s</div>
        <div class="play-overlay">
          <div class="play-icon-circle">
            <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"></polygon></svg>
          </div>
        </div>
      </div>
      <div class="clip-card-details">
        <h4 class="clip-hook-title" title="${clip.hook_title}">${clip.hook_title}</h4>
        <div class="clip-source-name">${clip.source_title}</div>
        <div class="clip-scores-breakdown">
          <div class="score-item">
            <span class="score-lbl">Gancho</span>
            <span class="score-val">${clip.hook_score || 90}</span>
          </div>
          <div class="score-item">
            <span class="score-lbl">Retenção</span>
            <span class="score-val">${clip.retention_score || 88}</span>
          </div>
          <div class="score-item">
            <span class="score-lbl">Compart.</span>
            <span class="score-val">${clip.shareability_score || 92}</span>
          </div>
        </div>
        <div class="clip-card-actions">
          <a href="${videoUrl}" download="${clip.hook_title}.mp4" class="btn-card-action btn-download" title="Baixar Vídeo">
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="7 10 12 15 17 10"></polyline><line x1="12" y1="15" x2="12" y2="3"></line></svg>
            Baixar
          </a>
          <button class="btn-card-delete" data-id="${clip.id}" title="Excluir">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
          </button>
        </div>
      </div>
    `;

    // Selection checkbox (don't open video modal when clicking checkbox)
    const selectBox = card.querySelector(".clip-select-box");
    const selectCb = card.querySelector(".clip-select-cb");
    selectBox.addEventListener("click", (e) => e.stopPropagation());
    selectCb.addEventListener("change", () => {
      if (selectCb.checked) selectedClipIds.add(clip.id);
      else selectedClipIds.delete(clip.id);
      refreshSelectionUI();
    });

    // Click on thumbnail opens player modal
    card.querySelector(".clip-thumb-box").addEventListener("click", () => {
      openVideoPlayer(clip);
    });

    // Delete button
    card.querySelector(".btn-card-delete").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (confirm(`Deseja realmente excluir o clipe "${clip.hook_title}"?`)) {
        await fetch(`/api/clips/${clip.id}`, { method: "DELETE" });
        loadGallery();
      }
    });

    return card;
  }

  btnRefreshGallery.addEventListener("click", loadGallery);

  // Video Modal Playback
  function openVideoPlayer(clip) {
    const videoUrl = `/api/clips/${clip.id}/video`;
    activeVideoPlayer.src = videoUrl;
    videoModalTitle.textContent = clip.hook_title;
    
    videoModalScores.innerHTML = `
      <div class="tag">Score Viral: <strong>${clip.virality_score}/100</strong></div>
      <div class="tag">Duração: <strong>${clip.duration}s</strong></div>
      <div class="tag">Estilo: <strong>${clip.caption_style || 'MrBeast'}</strong></div>
    `;

    btnDownloadActiveVideo.onclick = () => {
      const a = document.createElement("a");
      a.href = videoUrl;
      a.download = `${clip.hook_title}.mp4`;
      a.click();
    };

    btnCopyHashtags.onclick = () => {
      const tags = (clip.hashtags || ["#viral", "#shorts"]).join(" ");
      navigator.clipboard.writeText(`${clip.hook_title}\n\n${tags}`);
      btnCopyHashtags.textContent = "Tags Copiadas!";
      setTimeout(() => btnCopyHashtags.textContent = "Copiar Tags", 2000);
    };

    videoModal.style.display = "flex";
    activeVideoPlayer.play();
  }

  btnCloseVideoModal.addEventListener("click", () => {
    activeVideoPlayer.pause();
    activeVideoPlayer.src = "";
    videoModal.style.display = "none";
  });

  videoModal.addEventListener("click", (e) => {
    if (e.target === videoModal) {
      btnCloseVideoModal.click();
    }
  });

  // -------------------------------------------------------------------------
  // Settings Management
  // -------------------------------------------------------------------------
  async function loadSettings() {
    try {
      const res = await fetch("/api/settings");
      if (!res.ok) return;
      const s = await res.json();

      setAiProvider.value = s.ai_provider || "heuristic";
      setOllamaUrl.value = s.ollama_url || "http://localhost:11434";
      setOllamaModel.value = s.ollama_model || "llama3.2:latest";
      setGeminiKey.value = s.gemini_api_key || "";
      setGeminiModel.value = s.gemini_model || "gemini-2.5-flash";
      setOpenaiKey.value = s.openai_api_key || "";
      setOpenaiModel.value = s.openai_model || "gpt-4o-mini";
      setGroqKey.value = s.groq_api_key || "";
      setGroqModel.value = s.groq_model || "llama-3.3-70b-versatile";
      setWhisperModel.value = s.whisper_model || "base";
      setWhisperLang.value = s.whisper_language || "auto";
      if (setMaxJobs) setMaxJobs.value = String(s.max_concurrent_jobs || 1);
      if (setVideoEncoder) setVideoEncoder.value = s.video_encoder || "auto";
      if (setCleanupSource) setCleanupSource.checked = s.cleanup_source_after !== false;

      updateProviderFieldsVisibility(s.ai_provider);
      updateAiModeBadge(s.ai_provider);

      // Load hardware & encoder info
      try {
        const sysRes = await fetch("/api/system");
        if (sysRes.ok && hwInfo) {
          const sys = await sysRes.json();
          const gpuLabel = sys.gpu_encoding ? `GPU (${sys.encoder})` : `CPU (${sys.encoder})`;
          hwInfo.innerHTML =
            `<strong>CPU:</strong> ${sys.cpu_name} (${sys.cpu_threads} threads) • ` +
            `<strong>RAM:</strong> ${sys.ram_gb} GB • ` +
            `<strong>Codificador:</strong> ${gpuLabel} • ` +
            `<strong>Recomendado:</strong> ${sys.recommended_concurrent_jobs} vídeo(s) simultâneo(s) e ${sys.render_workers} render(s) por vídeo`;
        }
      } catch (e) {}

    } catch (err) {
      console.error("Failed to load settings:", err);
    }
  }

  setAiProvider.addEventListener("change", () => {
    updateProviderFieldsVisibility(setAiProvider.value);
  });

  function updateProviderFieldsVisibility(provider) {
    fieldsOllama.style.display = provider === "ollama" ? "block" : "none";
    fieldsGemini.style.display = provider === "gemini" ? "block" : "none";
    fieldsOpenai.style.display = provider === "openai" ? "block" : "none";
    fieldsGroq.style.display = provider === "groq" ? "block" : "none";
  }

  function updateAiModeBadge(provider) {
    const labels = {
      heuristic: "IA: Heurística / Whisper Local",
      ollama: "IA: Ollama Local (Llama 3)",
      gemini: "IA: Google Gemini",
      openai: "IA: OpenAI GPT-4o",
      groq: "IA: Groq Ultra-Fast"
    };
    currentAiMode.textContent = labels[provider] || "IA: Local";
  }

  settingsForm.addEventListener("submit", async (e) => {
    e.preventDefault();

    const payload = {
      ai_provider: setAiProvider.value,
      ollama_url: setOllamaUrl.value.trim(),
      ollama_model: setOllamaModel.value.trim(),
      gemini_api_key: setGeminiKey.value.trim(),
      gemini_model: setGeminiModel.value.trim(),
      openai_api_key: setOpenaiKey.value.trim(),
      openai_model: setOpenaiModel.value.trim(),
      groq_api_key: setGroqKey.value.trim(),
      groq_model: setGroqModel.value.trim(),
      whisper_model: setWhisperModel.value,
      whisper_language: setWhisperLang.value,
      max_concurrent_jobs: setMaxJobs ? parseInt(setMaxJobs.value) : 1,
      video_encoder: setVideoEncoder ? setVideoEncoder.value : "auto",
      cleanup_source_after: setCleanupSource ? setCleanupSource.checked : true
    };

    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });

      if (res.ok) {
        settingsSaveMsg.style.display = "inline";
        updateAiModeBadge(payload.ai_provider);
        setTimeout(() => settingsSaveMsg.style.display = "none", 3000);
      }
    } catch (err) {
      alert("Erro ao salvar configurações.");
    }
  });

  function refreshSelectionUI() {
    const n = selectedClipIds.size;
    if (selectedClipsCount) selectedClipsCount.textContent = n;
    if (btnPostSelected) btnPostSelected.style.display = n > 0 ? "inline-flex" : "none";
    const summary = document.getElementById("autopost-selected-summary");
    if (summary) {
      summary.textContent = n > 0
        ? `${n} clipe(s) selecionado(s) pronto(s) para envio/agendamento.`
        : "Nenhum clipe selecionado. Marque os clipes desejados na Galeria de Clipes.";
    }
  }

  if (btnSelectAllClips) {
    btnSelectAllClips.addEventListener("click", () => {
      const allSelected = galleryClipsCache.length > 0 && selectedClipIds.size === galleryClipsCache.length;
      selectedClipIds.clear();
      if (!allSelected) galleryClipsCache.forEach(c => selectedClipIds.add(c.id));
      document.querySelectorAll(".clip-select-cb").forEach(cb => {
        cb.checked = selectedClipIds.has(cb.dataset.id);
      });
      refreshSelectionUI();
    });
  }

  if (btnPostSelected) {
    btnPostSelected.addEventListener("click", () => {
      refreshSelectionUI();
      switchTab("tab-autopost");
    });
  }

  // -------------------------------------------------------------------------
  // Auto-Postagem (YouTube Shorts, TikTok & Webhook)
  // -------------------------------------------------------------------------
  const autopostBadge = document.getElementById("autopost-badge");
  const postScheduleMode = document.getElementById("post-schedule-mode");
  const postScheduleDtGroup = document.getElementById("post-schedule-dt-group");
  const postScheduleIntGroup = document.getElementById("post-schedule-int-group");
  const postStartDt = document.getElementById("post-start-dt");
  const postIntervalHours = document.getElementById("post-interval-hours");
  const btnEnqueuePosts = document.getElementById("btn-enqueue-posts");
  const btnRefreshQueue = document.getElementById("btn-refresh-queue");
  const autopostQueueList = document.getElementById("autopost-queue-list");

  const ytClientId = document.getElementById("yt-client-id");
  const ytClientSecret = document.getElementById("yt-client-secret");
  const ytPrivacy = document.getElementById("yt-privacy");
  const ytConnBadge = document.getElementById("yt-conn-badge");
  const btnConnectYt = document.getElementById("btn-connect-yt");
  const btnDisconnectYt = document.getElementById("btn-disconnect-yt");

  const ttClientKey = document.getElementById("tt-client-key");
  const ttClientSecret = document.getElementById("tt-client-secret");
  const ttPrivacy = document.getElementById("tt-privacy");
  const ttConnBadge = document.getElementById("tt-conn-badge");
  const btnConnectTt = document.getElementById("btn-connect-tt");
  const btnDisconnectTt = document.getElementById("btn-disconnect-tt");

  const whEnabled = document.getElementById("wh-enabled");
  const whUrl = document.getElementById("wh-url");
  const whSecret = document.getElementById("wh-secret");

  if (postScheduleMode) {
    postScheduleMode.addEventListener("change", () => {
      const sched = postScheduleMode.value === "schedule";
      postScheduleDtGroup.style.display = sched ? "block" : "none";
      postScheduleIntGroup.style.display = sched ? "block" : "none";
      if (sched && !postStartDt.value) {
        const d = new Date(Date.now() + 15 * 60000);
        postStartDt.value = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
      }
    });
  }

  async function loadSocialStatus() {
    try {
      const res = await fetch("/api/social/status");
      if (!res.ok) return;
      const st = await res.json();
      if (ytClientId) ytClientId.value = st.youtube.client_id || "";
      if (ytPrivacy) ytPrivacy.value = st.youtube.default_privacy || "public";
      if (ytConnBadge) {
        ytConnBadge.textContent = st.youtube.connected ? `✅ Conectado (${st.youtube.channel_title || "YouTube"})` : "Desconectado";
        ytConnBadge.style.color = st.youtube.connected ? "var(--accent-green, #10b981)" : "";
      }
      if (btnDisconnectYt) btnDisconnectYt.style.display = st.youtube.connected ? "inline-flex" : "none";

      if (ttClientKey) ttClientKey.value = st.tiktok.client_key || "";
      if (ttPrivacy) ttPrivacy.value = st.tiktok.default_privacy || "PUBLIC_TO_EVERYONE";
      if (ttConnBadge) {
        ttConnBadge.textContent = st.tiktok.connected ? `✅ Conectado (${st.tiktok.display_name || "TikTok"})` : "Desconectado";
        ttConnBadge.style.color = st.tiktok.connected ? "var(--accent-green, #10b981)" : "";
      }
      if (btnDisconnectTt) btnDisconnectTt.style.display = st.tiktok.connected ? "inline-flex" : "none";

      if (whEnabled) whEnabled.checked = !!st.webhook.enabled;
      if (whUrl) whUrl.value = st.webhook.url || "";
    } catch (e) {}
  }

  async function saveSocialCredentials() {
    const payload = {
      youtube: {
        client_id: ytClientId.value.trim(),
        client_secret: ytClientSecret.value.trim(),
        default_privacy: ytPrivacy.value,
      },
      tiktok: {
        client_key: ttClientKey.value.trim(),
        client_secret: ttClientSecret.value.trim(),
        default_privacy: ttPrivacy.value,
      },
      webhook: {
        enabled: whEnabled.checked,
        url: whUrl.value.trim(),
        secret: whSecret.value.trim(),
      },
    };
    const res = await fetch("/api/social/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) await loadSocialStatus();
  }

  document.getElementById("btn-save-social")?.addEventListener("click", async () => {
    await saveSocialCredentials();
    alert("Credenciais salvas com sucesso!");
  });
  document.getElementById("btn-save-webhook")?.addEventListener("click", async () => {
    await saveSocialCredentials();
    alert("Webhook salvo com sucesso!");
  });

  btnConnectYt?.addEventListener("click", async () => {
    await saveSocialCredentials();
    const res = await fetch("/api/social/youtube/auth-url");
    if (!res.ok) {
      const err = await res.json();
      return alert(err.detail || "Preencha Client ID e Client Secret do Google.");
    }
    const data = await res.json();
    window.location.href = data.url;
  });

  btnConnectTt?.addEventListener("click", async () => {
    await saveSocialCredentials();
    const res = await fetch("/api/social/tiktok/auth-url");
    if (!res.ok) {
      const err = await res.json();
      return alert(err.detail || "Preencha Client Key e Client Secret do TikTok.");
    }
    const data = await res.json();
    window.location.href = data.url;
  });

  btnDisconnectYt?.addEventListener("click", async () => {
    await fetch("/api/social/disconnect/youtube", { method: "POST" });
    loadSocialStatus();
  });
  btnDisconnectTt?.addEventListener("click", async () => {
    await fetch("/api/social/disconnect/tiktok", { method: "POST" });
    loadSocialStatus();
  });

  async function loadSocialQueue() {
    if (!autopostQueueList) return;
    try {
      const res = await fetch("/api/social/queue");
      if (!res.ok) return;
      const items = await res.json();
      if (autopostBadge) autopostBadge.textContent = items.filter(i => i.status === "scheduled" || i.status === "uploading").length;
      if (items.length === 0) {
        autopostQueueList.innerHTML = '<p class="status-meta">Nenhuma postagem na fila.</p>';
        return;
      }
      autopostQueueList.innerHTML = "";
      items.forEach((item) => {
        const dt = new Date(item.scheduled_at * 1000).toLocaleString("pt-BR");
        const platLabel = item.platform === "youtube_shorts" ? "▶️ YouTube Shorts" : "🎵 TikTok";
        const statusMap = {
          scheduled: "🕒 Agendado",
          uploading: "⬆️ Enviando...",
          published: "✅ Publicado",
          error: "❌ Erro",
        };
        const row = document.createElement("div");
        row.style.cssText = "display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px;border-radius:10px;background:rgba(255,255,255,0.04);";
        row.innerHTML = `
          <div>
            <strong>${item.clip_title}</strong>
            <div class="status-meta">${platLabel} • ${dt} • <strong>${statusMap[item.status] || item.status}</strong></div>
            ${item.error ? `<div style="color:#f87171;font-size:12px;margin-top:4px;">${item.error}</div>` : ""}
            ${item.published_url ? `<a href="${item.published_url}" target="_blank" style="font-size:12px;color:#38bdf8;">Abrir publicação ↗</a>` : ""}
          </div>
          <button type="button" class="btn-danger-outline btn-del-queue" data-id="${item.id}" style="padding:6px 10px;">Remover</button>
        `;
        row.querySelector(".btn-del-queue").addEventListener("click", async () => {
          await fetch(`/api/social/queue/${item.id}`, { method: "DELETE" });
          loadSocialQueue();
        });
        autopostQueueList.appendChild(row);
      });
    } catch (e) {}
  }

  btnEnqueuePosts?.addEventListener("click", async () => {
    if (selectedClipIds.size === 0) {
      alert("Selecione pelo menos 1 clipe na Galeria de Clipes primeiro.");
      return switchTab("tab-gallery");
    }
    const platforms = [];
    if (document.getElementById("post-plat-youtube")?.checked) platforms.push("youtube_shorts");
    if (document.getElementById("post-plat-tiktok")?.checked) platforms.push("tiktok");
    if (platforms.length === 0) return alert("Escolha ao menos 1 plataforma (YouTube Shorts ou TikTok).");

    let startTs = null;
    let intervalHrs = 0;
    if (postScheduleMode.value === "schedule") {
      if (!postStartDt.value) return alert("Informe a data/hora inicial do agendamento.");
      startTs = new Date(postStartDt.value).getTime() / 1000;
      intervalHrs = parseFloat(postIntervalHours.value || "0");
    }

    const res = await fetch("/api/social/queue/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        clip_ids: Array.from(selectedClipIds),
        platforms,
        start_timestamp: startTs,
        interval_hours: intervalHrs,
      }),
    });
    if (!res.ok) {
      const err = await res.json();
      return alert(err.detail || "Erro ao agendar postagens.");
    }
    selectedClipIds.clear();
    document.querySelectorAll(".clip-select-cb").forEach(cb => cb.checked = false);
    refreshSelectionUI();
    await loadSocialQueue();
    alert("Clipes enviados para a fila de Auto-Postagem!");
  });

  btnRefreshQueue?.addEventListener("click", loadSocialQueue);

  // Initial loads
  loadSettings();
  loadGallery();
  loadSocialStatus();
  loadSocialQueue();
  if (window.location.hash === "#tab-autopost") {
    switchTab("tab-autopost");
  }
});
