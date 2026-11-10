# 🎬 ClipStudio AI (MyCliping)
> Aplicativo **100% local** de corte e edição inteligente de **vídeos e transmissões ao vivo (lives)** para Shorts, TikTok e Instagram Reels, inspirado no [SupoClip](https://github.com/FujiwaraChoki/supoclip).

---

## 🌟 Por que esta versão é superior para uso local?

Enquanto o SupoClip original depende de Docker Compose, PostgreSQL, Redis, pnpm, Next.js e **não suporta transmissões ao vivo** (`LIVE_MESSAGE = "Live streams aren't supported"`), o **ClipStudio AI** foi projetado para:

1. **Clipar VÍDEOS e LIVES AO VIVO**:
   - **Vídeos Gravados**: YouTube, Twitch VODs, Kick VODs e upload de arquivos locais (`.mp4`, `.mov`, `.mkv`, `.webm`).
   - **Lives em Tempo Real**: Conecta em lives da **Twitch, YouTube Live, Kick ou links M3U8/HLS**, mantendo um buffer contínuo (DVR) na memória para você clipar os últimos 30 segundos, 1 minuto, 2 minutos ou 5 minutos instantaneamente sem travar a live!
2. **Execução 100% Local no Windows**:
   - Sem necessidade de Docker, banco de dados pesado ou configurações complexas.
   - Binário de alta performance FFmpeg 7.1 integrado.
3. **Transcrição de Fala Palavra por Palavra**:
   - `faster-whisper` embutido com CTranslate2 (roda diretamente no seu processador ou GPU com máxima velocidade).
4. **Inteligência Artificial Flexível**:
   - **100% Offline**: Algoritmo heurístico inteligente de análise de energia da voz, ganchos e cadência (funciona sem nenhuma chave de API e sem internet).
   - **IA Local com Ollama**: Suporte a modelos como Llama 3.2, Mistral, Gemma 2 e Qwen.
   - **Nuvem Opcional**: Suporte a Google Gemini, OpenAI (GPT-4o-mini) e Groq.
5. **Rastreamento Facial e Enquadramento 9:16**:
   - Usa **MediaPipe** para detectar e manter o rosto do orador perfeitamente centralizado no formato vertical de 9:16.
   - Modos adicionais: *Split-Screen* (facecam no topo e gameplay/conteúdo embaixo) e *Blurred Background* (fundo desfocado).
6. **Legendas Dinâmicas Estilo MrBeast e Alex Hormozi**:
   - Legendas animadas em `.ass` com efeito de karaokê (destaque colorido sincronizado na palavra que está sendo falada).
   - Título de Gancho Viral (*Hook Title*) no topo do vídeo.

---

## 🚀 Como Executar

### Opção 1: Pelo arquivo executável (Mais Fácil)
Dê um duplo clique no arquivo:
```cmd
start.bat
```
O aplicativo iniciará o servidor local e abrirá automaticamente seu navegador em `http://localhost:3107`.

### Opção 2: Pelo Terminal / PowerShell
```powershell
python run.py
```
Acesse no seu navegador: `http://localhost:3107`

---

## 🧭 Como Usar as Funcionalidades

### 1. 🎬 Clipar Vídeos Gravados (VODs e Uploads)
1. Acesse a aba **"Clipar Vídeos"**.
2. Cole o link do YouTube, Twitch VOD ou Kick (ou selecione a opção de arquivo local e arraste seu vídeo).
3. Escolha o estilo das legendas (*MrBeast*, *Hormozi*, *Cyber Neon*, *Fire Flame*, *Minimalista*).
4. Escolha o formato:
   - **9:16 Rastreamento de Rosto**: Enquadra o orador com IA.
   - **9:16 Split Screen**: Facecam em cima e jogo/tela embaixo.
   - **9:16 Fundo Desfocado**: Mantém a imagem 16:9 centralizada com ambient blur.
5. Clique em **"Gerar Clipes Virais Agora"**. O progresso em tempo real será exibido e, ao término, os vídeos estarão prontos para assistir e baixar.

---

### 2. 🔴 Clipar Lives em Tempo Real
1. Acesse a aba **"Clipar Lives"**.
2. Cole o link da transmissão (ex: `https://twitch.tv/gaules` ou live do YouTube).
3. Clique em **"Conectar na Live"**.
4. O ClipStudio iniciará um **buffer contínuo de gravação sem re-codificação** (quase 0% de uso de CPU).
5. Quando o streamer fizer uma jogada incrível ou contar uma história engraçada, clique em:
   - **⚡ 30s** (Últimos 30 segundos)
   - **⚡ 60s** (Último 1 minuto)
   - **⚡ 2m** (Últimos 2 minutos)
   - **⚡ 5m** (Últimos 5 minutos)
6. O clipe bruto é extraído em menos de 1 segundo e automaticamente enviado para a transcrição, corte de rosto 9:16 e legendagem!

---

### 3. 📁 Galeria de Clipes e Studio
- Todos os cortes gerados ficam salvos na pasta `data/clips/` e listados na aba **"Galeria de Clipes"**.
- Visualize o player vertical diretamente na interface.
- Veja a pontuação viral (Gancho, Retenção, Compartilhamento).
- Copie o título do gancho e as hashtags sugeridas com 1 clique.
- Baixe o arquivo `.mp4` em alta resolução pronto para postar no TikTok, Instagram Reels e YouTube Shorts.

---

### 4. ⚙️ Configurações & Motores de IA
Na aba **"Configurações & IA"**, você pode alternar entre:
- **Heurística Local Inteligente**: Totalmente offline e gratuito.
- **Ollama Local**: Conecte com seu Ollama (`http://localhost:11434`).
- **Google Gemini / OpenAI / Groq**: Adicione sua chave de API para obter raciocínio avançado de virality scoring.
- **Tamanho do Whisper**: Alterne entre `tiny` (ultrarrápido), `base` (recomendado), `small` ou `medium`.

---

## 📂 Estrutura do Projeto

```
mycliping/
├── app/
│   ├── config.py             # Configurações, diretórios e detecção do FFmpeg
│   ├── main.py               # API FastAPI, WebSockets e streaming de vídeo
│   └── core/
│       ├── downloader.py     # Download com yt-dlp para vídeos e VODs
│       ├── live_monitor.py   # Gravador DVR em tempo real para lives
│       ├── transcriber.py    # Transcrição Whisper com timestamps por palavra
│       ├── ai_analyzer.py    # Seletor de momentos virais (Ollama, Gemini, OpenAI, Heurística)
│       ├── face_tracker.py   # Rastreamento de rosto 9:16 com MediaPipe
│       ├── caption_engine.py # Motor de legendas animadas estilo MrBeast/Hormozi
│       ├── video_renderer.py # Renderizador FFmpeg com filtros de recorte e legendas
│       ├── clip_pipeline.py  # Orquestrador assíncrono do pipeline
│       └── storage.py        # Gerenciamento e indexação dos clipes
├── static/
│   ├── index.html            # Interface moderna com abas e player embutido
│   ├── style.css             # Design moderno com glassmorphism e tema escuro
│   └── app.js                # Lógica e comunicação WebSocket do cliente
├── data/
│   ├── clips/                # Clipes gerados e miniaturas
│   ├── live_buffer/          # Buffer circular de gravação das lives
│   └── uploads/              # Vídeos locais enviados
├── run.py                    # Script de inicialização em Python
├── start.bat                 # Inicializador em 1 clique para Windows
└── requirements.txt          # Dependências do projeto
```

---

*Desenvolvido com foco em velocidade, autonomia e zero taxas ou marcas d'água.*
