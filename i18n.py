"""界面语言（中文 / English / Deutsch）。

不用逐行改界面代码：在 tkinter 创建控件、设置文字、弹对话框、写日志的地方统一翻译。
下拉框里显示翻译后的选项，程序内部 .get() 拿到的仍是中文原文，所以逻辑完全不受影响。
底层组件输出的少量技术日志可能仍是中文。
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
LANG_FILE = HERE / ".tools" / "language.txt"
LANGUAGES = {"zh": "中文", "en": "English", "de": "Deutsch"}
LANG = "zh"

# 中文 | English | Deutsch
_TABLE = r"""
视频提取|Video to audio|Video → Audio
演唱会降噪|Concert cleanup|Konzert-Bereinigung
电脑录音|Record PC audio|PC-Audio aufnehmen
音质增强|Enhance audio|Audio verbessern
提取 · 录制 · 增强，全程在本机处理|Extract · record · enhance — everything stays on this PC|Extrahieren · Aufnehmen · Verbessern – alles lokal auf diesem PC
准备就绪|Ready|Bereit
开始提取|Start|Starten
取消|Cancel|Abbrechen
打开文件|Open file|Datei öffnen
从视频保存音频|Save audio from a video|Audio aus einem Video speichern
视频链接|Video link|Video-Link
剪贴板里没有文字。|The clipboard is empty.|Die Zwischenablage ist leer.
粘贴|Paste|Einfügen
保存到|Save to|Speichern in
浏览…|Browse…|Durchsuchen…
格式|Format|Format
音质|Quality|Qualität
登录|Login|Anmeldung
只保存一段|Only save a part|Nur einen Teil speichern
从|From|Von
到|to|bis
例：1:30；结束留空=到结尾|e.g. 1:30; leave end empty = until the end|z. B. 1:30; Ende leer = bis zum Schluss
下载完直接送去“演唱会降噪”|Send to “Concert cleanup” when done|Danach direkt an „Konzert-Bereinigung“ senden
请选择保存位置。|Please choose where to save.|Bitte einen Speicherort wählen.
原始格式（不转码）|Original (no re-encoding)|Original (ohne Umkodierung)
高品质|High quality|Hohe Qualität
标准|Standard|Standard
节省空间|Small files|Platzsparend
不登录|Not logged in|Nicht angemeldet
cookies.txt 文件|cookies.txt file|cookies.txt-Datei
全部电脑声音|All PC audio|Gesamter PC-Ton
只录一个程序|Only one program|Nur ein Programm
除了一个程序都录|Everything except one program|Alles außer einem Programm
准备就绪 · Ctrl+Alt+R 开始/停止 · Ctrl+Alt+P 暂停/继续|Ready · Ctrl+Alt+R start/stop · Ctrl+Alt+P pause/resume|Bereit · Strg+Alt+R Start/Stopp · Strg+Alt+P Pause/Weiter
录音组件：检查中|Recorder: checking|Rekorder: wird geprüft
录音组件：已安装|Recorder: installed|Rekorder: installiert
录音组件：未安装|Recorder: not installed|Rekorder: nicht installiert
录音组件：正在安装……|Recorder: installing…|Rekorder: wird installiert…
重新准备录音组件|Rebuild recorder|Rekorder neu einrichten
● 开始录音|● Record|● Aufnehmen
暂停|Pause|Pause
继续|Resume|Weiter
停止|Stop|Stopp
正在启动 WASAPI 录音……|Starting WASAPI recording…|WASAPI-Aufnahme wird gestartet…
录制电脑声音|Record what your PC plays|Aufnehmen, was der PC abspielt
WASAPI 数字环回，不经过麦克风|WASAPI digital loopback, no microphone|WASAPI-Digital-Loopback, ohne Mikrofon
录制范围|Capture|Aufnahmebereich
目标程序|Program|Programm
刷新|Refresh|Aktualisieren
保存格式|Save as|Speichern als
自动去掉首尾静音|Trim silence at start/end|Stille am Anfang/Ende entfernen
按静音自动分段|Split at silences|Bei Stille aufteilen
音量标准化|Normalize loudness|Lautheit normalisieren
定时停止|Stop after|Stoppen nach
静音自动停止|Stop after silence|Bei Stille stoppen
秒|s|s
 秒| s| s
启用全局快捷键|Enable global hotkeys|Globale Tastenkürzel aktivieren
请选择目标程序。|Please choose a program.|Bitte ein Programm wählen.
定时、静音时间或目标程序设置不正确。|Timer, silence time or program is not valid.|Timer, Stillezeit oder Programm ist ungültig.
录音已开始。|Recording started.|Aufnahme gestartet.
正在录音 · |Recording · |Aufnahme · 
正在录音|Recording|Aufnahme läuft
已暂停|Paused|Pausiert
正在停止……|Stopping…|Wird gestoppt…
正在完成后期处理……|Finishing post-processing…|Nachbearbeitung wird abgeschlossen…
后期处理失败|Post-processing failed|Nachbearbeitung fehlgeschlagen
录音失败|Recording failed|Aufnahme fehlgeschlagen
录音组件已就绪。|Recorder is ready.|Rekorder ist bereit.
录音组件异常退出|Recorder exited unexpectedly|Rekorder unerwartet beendet
录音结束，但没有找到临时 WAV 文件。|Recording ended but the temporary WAV file is missing.|Aufnahme beendet, aber die temporäre WAV-Datei fehlt.
找不到 ffmpeg，无法完成录音后期处理。|ffmpeg not found, post-processing is not possible.|ffmpeg nicht gefunden, Nachbearbeitung nicht möglich.
录音仍在进行。确定停止并退出吗？|Still recording. Stop and quit?|Die Aufnahme läuft noch. Stoppen und beenden?
完成 · 共 |Done · |Fertig · 
 个文件| files| Dateien
AI 人声增强|AI voice enhancement|KI-Sprachverbesserung
清晰增强（传统）|Clarity (classic)|Klarheit (klassisch)
AI 模型在本机运行，适合语音降噪和清晰度提升|The AI model runs on this PC – good for speech noise reduction and clarity|Das KI-Modell läuft lokal – gut für Sprach-Rauschunterdrückung und Klarheit
AI 不会恢复原本不存在的细节；人声模型不建议用于音乐母带|AI cannot restore details that were never recorded; the voice model is not meant for music masters|KI kann nie aufgenommene Details nicht zurückholen; das Sprachmodell ist nicht für Musik-Master gedacht
输入音频|Input audio|Eingabe-Audio
选择…|Choose…|Auswählen…
模式|Mode|Modus
响度标准化|Normalize loudness|Lautheit normalisieren
AI 降噪强度|AI noise reduction|KI-Rauschunterdrückung
轻（保留一点环境声）|Light (keep some ambience)|Leicht (etwas Raumklang behalten)
中|Medium|Mittel
强（去掉全部噪声）|Strong (remove all noise)|Stark (alles Rauschen entfernen)
开始增强|Enhance|Verbessern
安装 AI 组件|Install AI|KI installieren
重新下载 AI 组件|Download AI again|KI erneut herunterladen
正在安装 AI 组件……|Installing AI…|KI wird installiert…
AI 组件：已安装（DeepFilterNet3）|AI: installed (DeepFilterNet3)|KI: installiert (DeepFilterNet3)
AI 组件：未安装（传统增强可以直接使用）|AI: not installed (classic mode works without it)|KI: nicht installiert (klassischer Modus geht auch ohne)
正在增强……|Enhancing…|Wird verbessert…
增强完成|Enhancement finished|Verbesserung fertig
增强失败|Enhancement failed|Verbesserung fehlgeschlagen
AI 组件安装完成|AI installed|KI installiert
AI 组件安装失败|AI installation failed|KI-Installation fehlgeschlagen
请选择有效的输入音频。|Please choose a valid input file.|Bitte eine gültige Eingabedatei wählen.
找不到 ffmpeg，请重新运行 start.bat。|ffmpeg not found – please run start.bat again.|ffmpeg nicht gefunden – bitte start.bat erneut ausführen.
从现场视频里取出声音，用音乐专用 AI 去掉观众尖叫、鼓掌和底噪，尽量保持原声|Music AI removes crowd, applause and hiss from concert videos – the original sound stays|Musik-KI entfernt Publikum, Applaus und Rauschen aus Konzertvideos – der Originalklang bleibt
视频/音频|Video/audio|Video/Audio
去观众声|Remove crowd|Publikum entfernen
去底噪|Remove hiss|Rauschen entfernen
音质修复（补高音）|Repair highs|Höhen reparieren
分离人声和伴奏|Split vocals|Gesang trennen
去混响（可调现场感）|De-reverb|Hall trennen
同时生成视频（画面不重新压缩）|Also make video|Auch Video erzeugen
音量|Loudness|Lautstärke
和原视频一样响（推荐）|Like original (recommended)|Wie Original (empfohlen)
统一到 -14 LUFS（流媒体标准）|-14 LUFS (streaming standard)|-14 LUFS (Streaming-Standard)
不调整|Unchanged|Unverändert
另存人声、伴奏分轨|Save stems|Spuren speichern
整体|Overall|Gesamt
人声与伴奏（间奏）|Vocals & backing|Gesang & Begleitung
空间 / 参考曲|Space / reference|Raum / Referenz
降噪强度|Cleanup strength|Stärke der Bereinigung
自动音色校正|Auto tone correction|Auto-Klangkorrektur
低频（轰）|Bass (boom)|Bass (Dröhnen)
中频（人声/吉他）|Mids (voice/guitar)|Mitten (Stimme/Gitarre)
高频（清晰）|Treble (clarity)|Höhen (Klarheit)
补回的高音（音质修复）|Repaired highs|Reparierte Höhen
人声音量|Vocal level|Gesang-Pegel
伴奏音量|Backing level|Begl. Pegel
伴奏低频（轰）|Backing bass|Begl. Bass
伴奏刺耳（2–5 kHz）|Backing harsh (2–5 kHz)|Begl. Schärfe (2–5 kHz)
伴奏高频|Backing treble|Begl. Höhen
伴奏动态柔化|Backing soften (dynamic)|Begl. dynamisch glätten
只影响伴奏，人声不变（需勾选“分离人声和伴奏”）|Backing only – vocals unchanged (needs “Split vocals”)|Nur Begleitung – Gesang bleibt (braucht „Gesang trennen“)
立体声宽度|Stereo width|Stereobreite
现场感（干 ↔ 空间）|Room (dry ↔ spacious)|Raum (trocken ↔ weit)
耳机空间音频（戴耳机听像在场馆里）|Headphone 3D (sounds like being in the venue)|Kopfhörer-3D (klingt wie in der Halle)
参考曲：|Reference:|Referenz:
未选择|none|keine
清除|Clear|Entfernen
参考曲选同一首歌的录音室版；现场感往左需勾“去混响”|Reference = studio version; “Room” left needs De-reverb|Referenz = Studioversion; „Raum“ links: Hall trennen
选择参考曲（同一首歌的录音室版）|Choose a reference (studio version of the same song)|Referenz wählen (Studioversion desselben Songs)
正在分析参考曲……|Analysing reference…|Referenz wird analysiert…
参考曲已设置：|Reference set: |Referenz gesetzt: 
（自动音色校正会照着它来）| (auto tone correction will follow it)| (die Auto-Klangkorrektur richtet sich danach)
默认|Default|Standard
存为预设|Save preset|Preset speichern
删除|Delete|Löschen
恢复默认|Reset|Zurücksetzen
删除预设“|Delete preset “|Preset löschen „
给这组设置起个名字（比如歌手名）：|Name these settings (e.g. the artist):|Name für diese Einstellungen (z. B. Künstler):
试听：从|Preview from|Vorhören ab
开始的 30 秒|for 30 s|für 30 s
生成试听（AI）|Make preview (AI)|Vorschau (KI)
▶ 原声|▶ Original|▶ Original
▶ 调整后|▶ Adjusted|▶ Angepasst
按住=听原声|Hold = original|Halten = Original
■ 停止|■ Stop|■ Stopp
开始处理整段|Process whole file|Ganze Datei verarbeiten
批量处理…|Batch…|Stapel…
按当前设置重新导出|Re-export|Neu exportieren
设置…|Settings…|Einstellungen…
安装演唱会降噪组件|Install concert AI|Konzert-KI installieren
安装演唱会降噪组件（约 5 GB）|Install concert AI (≈5 GB)|Konzert-KI installieren (≈5 GB)
重新安装组件|Reinstall|Neu installieren
AI 组件：已安装|AI: installed|KI: installiert
AI 组件：未安装，第一次使用请点右边的按钮|AI: not installed – click the button on the right first|KI: nicht installiert – zuerst rechts auf den Knopf klicken
正在安装演唱会降噪组件……|Installing concert AI…|Konzert-KI wird installiert…
组件安装完成，可以开始了|Installed – ready to go|Installiert – bereit
组件安装失败|Installation failed|Installation fehlgeschlagen
将下载并安装演唱会降噪组件（约 5 GB，需要联网，可能要 10～40 分钟）。\n安装完成后可以离线使用。现在开始吗？|This downloads and installs the concert AI (≈5 GB, internet needed, 10–40 minutes).\nAfterwards it works offline. Start now?|Die Konzert-KI wird heruntergeladen und installiert (≈5 GB, Internet nötig, 10–40 Minuten).\nDanach funktioniert sie offline. Jetzt starten?
需要先安装演唱会降噪组件。|Please install the concert AI first.|Bitte zuerst die Konzert-KI installieren.
第一次使用需要先安装演唱会降噪组件。|Please install the concert AI first.|Bitte zuerst die Konzert-KI installieren.
请先选择一个视频或音频文件。|Please choose a video or audio file first.|Bitte zuerst eine Video- oder Audiodatei wählen.
选择一段演唱会视频，先试听 30 秒，满意再处理整段|Choose a concert video, preview 30 s, then process the whole file|Konzertvideo wählen, 30 s vorhören, dann alles verarbeiten
正在生成 30 秒试听……|Making a 30 s preview…|30-s-Vorschau wird erstellt…
正在处理整段……|Processing the whole file…|Ganze Datei wird verarbeitet…
正在按新设置生成试听……|Updating the preview…|Vorschau wird aktualisiert…
已按新设置更新试听|Preview updated|Vorschau aktualisiert
正在按当前设置重新导出（不用重跑 AI）……|Re-exporting (no AI needed)…|Neuer Export (ohne KI)…
正在取消……|Cancelling…|Wird abgebrochen…
已取消|Cancelled|Abgebrochen
处理失败|Processing failed|Verarbeitung fehlgeschlagen
选择要批量处理的视频（可多选）|Choose videos for batch processing (multiple allowed)|Videos für die Stapelverarbeitung wählen (mehrere möglich)
试听已生成：拖动滑块后点“▶ 调整后”；按住“按住=听原声”可在同一时间点对比|Preview ready: move the sliders and click “▶ Adjusted”; hold “Hold = original” to compare at the same moment|Vorschau fertig: Regler bewegen und „▶ Angepasst“ klicken; „Halten = Original“ halten zum direkten Vergleich
完成。想换个音色？调好滑块后点“按当前设置重新导出”，几秒就好|Done. Want a different sound? Adjust the sliders and click “Re-export” – takes seconds|Fertig. Anderer Klang? Regler anpassen und „neu exportieren“ klicken – dauert Sekunden
演唱会降噪 · 设置|Concert cleanup · Settings|Konzert-Bereinigung · Einstellungen
中间文件放在哪（整场演唱会每小时约 3 GB，处理完同类的新文件时会自动删掉旧的）|Where to keep temporary files (≈3 GB per hour of concert; old ones are removed automatically)|Ort für temporäre Dateien (≈3 GB pro Konzertstunde; alte werden automatisch gelöscht)
留空 = 系统临时文件夹（一般在 C 盘）|Empty = system temp folder (usually on C:)|Leer = System-Temp-Ordner (meist auf C:)
显卡半精度加速（约快 1.5–2 倍；关掉则和旧版逐位一致）|GPU half precision (≈1.5–2× faster; off = bit-identical to before)|GPU-Halbpräzision (≈1,5–2× schneller; aus = bitgleich wie vorher)
保存|Save|Speichern
这个文件夹不存在。|This folder does not exist.|Dieser Ordner existiert nicht.
时间轴：选好文件后显示每一秒的响度（灰）和刺耳度（红）；点一下就从那里试听|Timeline: loudness (grey) and harshness (red) per second; click to preview from there|Zeitleiste: Lautheit (grau) und Schärfe (rot) pro Sekunde; klicken zum Vorhören ab dort
灰=响度 红=刺耳|grey=loud red=harsh|grau=laut rot=scharf
 绿=人声| green=vocals| grün=Gesang
 蓝框=试听段 · 点击跳转| blue=preview| blau=Vorschau
橙线 = 你设置的均衡\n生成试听后，这里会显示处理前后的频谱对比|Orange = your EQ\nAfter a preview this shows the spectrum before/after|Orange = dein EQ\nNach der Vorschau siehst du hier das Spektrum vorher/nachher
紫虚线 = 伴奏均衡|purple = backing EQ|lila = Begl.-EQ
压缩截止|cut-off|Grenze
原声|Original|Original
调整后|Adjusted|Angepasst
均衡|EQ|EQ
导出|Export|Export
音频|Audio|Audio
所有文件|All files|Alle Dateien
视频或音频|Video or audio|Video oder Audio
完成|Done|Fertig
失败|Failed|Fehlgeschlagen
正在处理音频……|Processing audio…|Audio wird verarbeitet…
正在下载…… |Downloading… |Lädt… 
未知错误|Unknown error|Unbekannter Fehler
请填写开始时间或结束时间。|Please enter a start or end time.|Bitte Start- oder Endzeit eingeben.
结束时间必须晚于开始时间。|The end must be after the start.|Das Ende muss nach dem Start liegen.
Audio Studio 启动失败：\n|Audio Studio could not start:\n|Audio Studio konnte nicht starten:\n
语言|Language|Sprache
检查更新|Check for updates|Nach Updates suchen
有新版本 · 点此更新|Update available · click|Update verfügbar · klicken
已是最新版|Up to date|Aktuell
正在更新……|Updating…|Wird aktualisiert…
发现新版本。现在下载并更新吗？\n只替换程序文件，AI 组件、模型、设置和你的文件都不动；更新完会自动重启。|A new version is available. Download and update now?\nOnly program files are replaced – AI, models, settings and your files stay; the app restarts afterwards.|Eine neue Version ist verfügbar. Jetzt aktualisieren?\nNur Programmdateien werden ersetzt – KI, Modelle, Einstellungen und deine Dateien bleiben; danach Neustart.
这个文件夹是 git 仓库：请在 GitHub Desktop 里点 “Fetch origin” → “Pull origin” 更新。|This folder is a git repository: update it in GitHub Desktop with “Fetch origin” → “Pull origin”.|Dieser Ordner ist ein Git-Repository: in GitHub Desktop mit „Fetch origin“ → „Pull origin“ aktualisieren.
语言 / Language|Language|Sprache
扒谱|Transcribe|Transkription
扒谱与声部分析|Transcription & parts analysis|Transkription & Stimmenanalyse
AI 把录音扒成总谱和分谱（MusicXML / PDF，初稿需校对），并画出每个声部什么时候响、多响|AI turns a recording into a full score and parts (MusicXML / PDF – a draft to proofread) and maps when and how loud each part plays|KI macht aus einer Aufnahme Partitur und Stimmen (MusicXML / PDF – Entwurf zum Korrigieren) und zeigt, wann und wie laut jede Stimme spielt
音频/视频|Audio/video|Audio/Video
先去观众声和底噪（现场录音推荐）|Clean crowd & hiss first (live recordings)|Erst Publikum & Rauschen entfernen (Live)
精度|Accuracy|Genauigkeit
最准（大模型 + 束搜索，慢）|Best (large + beam search, slow)|Beste (groß + Beam-Suche, langsam)
较准（大模型）|Good (large model)|Gut (großes Modell)
快速（中模型）|Fast (medium model)|Schnell (mittleres Modell)
乐器：自动识别|Instruments: auto|Instrumente: automatisch
指定乐器…|Instruments…|Instrumente…
指定乐器（知道编制时选上，识别会更准）|Instruments (choosing them makes recognition more accurate)|Instrumente (Auswahl macht die Erkennung genauer)
只勾这场演出里真的有的乐器；不勾 = 让 AI 自己判断|Only tick instruments that really play; none ticked = let the AI decide|Nur tatsächlich spielende Instrumente ankreuzen; keins = KI entscheidet
确定|OK|OK
① 识别音符|① Recognise notes|① Noten erkennen
声部音量地图|Parts loudness map|Stimmen-Lautstärkekarte
识别到的声部（勾选要输出的，右边选记成什么乐器）|Detected parts (tick the ones to export, choose the notated instrument)|Erkannte Stimmen (auswählen und Notationsinstrument festlegen)
细致节奏（到 32 分音符）|Fine rhythm (down to 32nds)|Feiner Rhythmus (bis 32tel)
用 MuseScore 导出 PDF|Export PDF with MuseScore|PDF mit MuseScore exportieren
② 生成总谱和分谱|② Make score & parts|② Partitur & Stimmen erzeugen
打开文件夹|Open folder|Ordner öffnen
Hugging Face 授权…|Hugging Face access…|Hugging-Face-Zugang…
安装扒谱组件|Install transcription|Transkription installieren
扒谱组件：已安装|Transcription: installed|Transkription: installiert
扒谱组件：未安装|Transcription: not installed|Transkription: nicht installiert
授权：已设置|Access: set|Zugang: gesetzt
授权：未设置|Access: not set|Zugang: fehlt
MuseScore：已找到|MuseScore: found|MuseScore: gefunden
MuseScore：未找到（只导出 MusicXML）|MuseScore: not found (MusicXML only)|MuseScore: nicht gefunden (nur MusicXML)
选一段演奏录音：先“识别音符”，再选要哪些声部、记成什么乐器，生成总谱和分谱|Choose a recording: “Recognise notes”, pick parts and instruments, then make score & parts|Aufnahme wählen: „Noten erkennen“, Stimmen und Instrumente wählen, dann Partitur & Stimmen erzeugen
正在识别音符……|Recognising notes…|Noten werden erkannt…
正在生成总谱和分谱……|Making score & parts…|Partitur & Stimmen werden erzeugt…
正在分离六个声部……|Separating six stems…|Sechs Spuren werden getrennt…
正在安装扒谱组件……|Installing transcription…|Transkription wird installiert…
扒谱组件安装完成|Transcription installed|Transkription installiert
请先点右下角“安装扒谱组件”。|Please click “Install transcription” (bottom right) first.|Bitte zuerst unten rechts „Transkription installieren“ klicken.
请至少勾选一个声部。|Please tick at least one part.|Bitte mindestens eine Stimme auswählen.
Hugging Face 授权|Hugging Face access|Hugging-Face-Zugang
打开网页|Open web page|Webseite öffnen
令牌一般以 hf_ 开头，请检查一下。|Tokens usually start with hf_ – please check.|Tokens beginnen meist mit hf_ – bitte prüfen.
声部音量地图已生成：颜色越深越响；最上面一行是每一秒最响的声部|Map ready: darker = louder; the top row shows the loudest part each second|Karte fertig: dunkler = lauter; oben die jeweils lauteste Stimme
请先在“演唱会降噪”页安装 AI 组件（扒谱和它共用显卡环境）。|Please install the AI on the “Concert cleanup” tab first (shared GPU environment).|Bitte zuerst im Tab „Konzert-Bereinigung“ die KI installieren (gemeinsame GPU-Umgebung).
最响|loudest|lauteste
"""

# 动态文字里的片段（f-string 拼出来的状态、日志），按从长到短替换
_FRAGMENTS = r"""
时长 |Length |Länge 
 分钟| min| Min.
（含画面，可生成降噪后的视频）| (with picture – a cleaned video can be made)| (mit Bild – ein bereinigtes Video ist möglich)
（纯音频）| (audio only)| (nur Audio)
第 |Part |Teil 
 段 · | · | · 
 个 · | · | · 
批量处理结束：成功 |Batch finished: |Stapel fertig: 
，失败 |, failed |, fehlgeschlagen 
，未处理 |, skipped |, übersprungen 
 个（已取消）| (cancelled)| (abgebrochen)
 个||
拖进了 |Dropped |Abgelegt: 
 个文件，已选第一个。要一次处理多个，请用“批量处理…”| files – using the first. For several at once use “Batch…”| Dateien – die erste wird verwendet. Für mehrere „Stapel…“ benutzen
已从“视频提取”收到：|Received from “Video to audio”: |Von „Video → Audio“ erhalten: 
。先点“生成试听（AI）”试试效果。|. Click “Make preview (AI)” to try it.|. „Vorschau (KI)“ klicken zum Ausprobieren.
正在分析音量……|Measuring loudness…|Lautheit wird gemessen…
正在取出音频……|Extracting audio…|Audio wird extrahiert…
使用显卡：|Using GPU: |GPU: 
使用：|Using: |Verwendet: 
检测到压缩截止频率约 |Codec cut-off detected at about |Codec-Grenze bei etwa 
 kHz，只补这以上的部分。| kHz – only the band above it is repaired.| kHz – nur darüber wird repariert.
这段声音的高音是完整的，不需要音质修复。|The highs are complete – no repair needed.|Die Höhen sind vollständig – keine Reparatur nötig.
已生成音频：|Audio saved: |Audio gespeichert: 
已生成视频：|Video saved: |Video gespeichert: 
正在把处理后的声音放回视频（画面不重新压缩）……|Putting the new sound into the video (picture not re-encoded)…|Neuer Ton wird ins Video gelegt (Bild unverändert)…
分析音量|measuring|Messung
去观众声|removing crowd|Publikum entfernen
去底噪|removing hiss|Rauschen entfernen
音质修复|repairing highs|Höhen reparieren
分离人声和伴奏|splitting vocals|Gesang trennen
分离混响|splitting reverb|Hall trennen
AI 处理完成|AI finished|KI fertig
下载模型 |Downloading model |Modell wird geladen 
第一次使用，正在下载模型 |First use – downloading model |Erste Nutzung – Modell wird geladen 
（约 900 MB）……| (≈900 MB)…| (≈900 MB)…
显存不够，改为一次只放一个模型（会慢一点）。|Not enough GPU memory – loading one model at a time (a bit slower).|Zu wenig GPU-Speicher – ein Modell nach dem anderen (etwas langsamer).
 大调| major| Dur
 小调| minor| Moll
约 |≈ |≈ 
正在录音 · |Recording · |Aufnahme · 
完成 · 共 |Done · |Fertig · 
识别到 |Found |Gefunden: 
 个声部| parts| Stimmen
。勾选要输出的声部，再点“② 生成总谱和分谱”|. Tick the parts to export, then click “② Make score & parts”|. Stimmen auswählen, dann „② Partitur & Stimmen erzeugen“
拍号 |time |Takt 
速度约 |tempo ≈ |Tempo ≈ 
已生成：总谱 + |Done: score + |Fertig: Partitur + 
 份分谱| parts| Stimmen
，保存在 |, saved in |, gespeichert in 
（调性 | (key | (Tonart 
 个音 · | notes · | Noten · 
失败：|Failed: |Fehlgeschlagen: 
识别音符 |Recognising notes |Noten erkennen 
 段（每段 5 秒）| chunks (5 s each)| Abschnitte (je 5 s)
找节拍和小节线（Beat This!）|Finding beats and bar lines (Beat This!)|Schläge und Taktstriche (Beat This!)
加载扒谱模型（第一次会下载，大模型约 1.4 GB）|Loading model (first time downloads ≈1.4 GB)|Modell wird geladen (beim ersten Mal ≈1,4 GB)
整理声部 |Arranging part |Stimme 
读入音符并对齐到节拍网格|Reading notes and snapping to the beat grid|Noten einlesen und am Raster ausrichten
写出总谱|Writing the score|Partitur wird geschrieben
用 MuseScore 导出 PDF |Exporting PDF with MuseScore |PDF mit MuseScore 
分离六个声部（人声/鼓/贝斯/吉他/钢琴/其他）|Separating six stems (vocals/drums/bass/guitar/piano/other)|Sechs Spuren trennen (Gesang/Schlagzeug/Bass/Gitarre/Klavier/Rest)
识别到调性：|Key detected: |Erkannte Tonart: 
（活动）| (activity)| (Aktivität)
"""


def _parse(block: str) -> dict:
    table = {}
    for line in block.split("\n"):
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) != 3:
            continue
        zh, en, de = (p.replace("\\n", "\n") for p in parts)
        table[zh] = (en, de)
    return table


TABLE = _parse(_TABLE)
# 句子里的片段：动态片段 + 表里 4 个字以上的整句/按钮名，一律从长到短替换（避免短词先把长句拆坏）
FRAGMENTS = sorted(list(_parse(_FRAGMENTS).items()) + [(k, v) for k, v in TABLE.items() if len(k) >= 4],
                   key=lambda item: -len(item[0]))
_REVERSE: dict[str, str] = {}


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def tr(text):
    """中文 → 当前语言。不认识的文字原样返回。"""
    if LANG == "zh" or not isinstance(text, str) or not _has_cjk(text):
        return text
    index = 0 if LANG == "en" else 1
    if text in TABLE:
        result = TABLE[text][index]
        _REVERSE[result] = text
        return result
    result = text
    for zh, pair in FRAGMENTS:
        if zh in result:
            result = result.replace(zh, pair[index])
    for zh, western in (("：", ": "), ("，", ", "), ("（", " ("), ("）", ")"), ("。", ". "), ("“", "“"), ("…", "…")):
        result = result.replace(zh, western)
    return result.replace("  ", " ")


def untr(text):
    """当前语言 → 中文（给程序内部逻辑用）。"""
    return _REVERSE.get(text, text) if isinstance(text, str) else text


def load_language() -> str:
    try:
        value = LANG_FILE.read_text(encoding="utf-8").strip()
        return value if value in LANGUAGES else "zh"
    except OSError:
        return "zh"


def save_language(code: str) -> None:
    try:
        LANG_FILE.parent.mkdir(parents=True, exist_ok=True)
        LANG_FILE.write_text(code, encoding="utf-8")
    except OSError:
        pass


def install(code: str | None = None) -> None:
    """在创建任何窗口之前调用。"""
    global LANG
    LANG = code or load_language()
    if LANG == "zh":
        return
    import tkinter
    import tkinter.messagebox as messagebox
    import tkinter.simpledialog as simpledialog
    from tkinter import filedialog, ttk

    original_options = tkinter.Misc._options

    def options(self, cnf, kw=None):
        def fix(d):
            if isinstance(d, dict):
                d = dict(d)
                for key in ("text", "title"):
                    if key in d:
                        d[key] = tr(d[key])
                if "values" in d and isinstance(d["values"], (list, tuple)):
                    d["values"] = [tr(v) for v in d["values"]]
            return d
        return original_options(self, fix(cnf), fix(kw))

    tkinter.Misc._options = options

    original_format = ttk._format_optdict

    def format_optdict(optdict, script=False, ignore=None):
        if "text" in optdict:
            optdict = dict(optdict, text=tr(optdict["text"]))
        return original_format(optdict, script, ignore)

    ttk._format_optdict = format_optdict

    original_title = tkinter.Wm.wm_title

    def title(self, string=None):
        return original_title(self, tr(string) if string else string)

    tkinter.Wm.wm_title = tkinter.Wm.title = title

    original_show = messagebox._show

    def show(title=None, message=None, *args, **kwargs):
        return original_show(tr(title), tr(message), *args, **kwargs)

    messagebox._show = show

    original_ask = simpledialog.askstring
    simpledialog.askstring = lambda title, prompt, **kw: original_ask(tr(title), tr(prompt), **kw)

    for name in ("askopenfilename", "askopenfilenames", "askdirectory", "asksaveasfilename"):
        original = getattr(filedialog, name)

        def wrapper(*args, _original=original, **kw):
            if "title" in kw:
                kw["title"] = tr(kw["title"])
            if "filetypes" in kw:
                kw["filetypes"] = [(tr(label), pattern) for label, pattern in kw["filetypes"]]
            return _original(*args, **kw)

        setattr(filedialog, name, wrapper)

    original_insert = tkinter.Text.insert
    tkinter.Text.insert = lambda self, index, chars, *args: original_insert(self, index, tr(chars), *args)

    original_set, original_get = tkinter.Variable.set, tkinter.StringVar.get
    tkinter.StringVar.set = tkinter.StringVar.initialize = lambda self, value: original_set(self, tr(value))
    tkinter.StringVar.get = lambda self: untr(original_get(self))
