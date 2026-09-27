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
AI 组件：未安装，请到左下角“设置”里安装|AI: not installed – install it under “Settings” (bottom left)|KI: nicht installiert – unter „Einstellungen“ (unten links) installieren
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
较快（大模型，多段并行）|Faster (large model, parallel chunks)|Schneller (großes Modell, parallel)
只扒一段|Only a section|Nur ein Abschnitt
例：1:30；整场演出只扒想要的那首会快很多|e.g. 1:30 – transcribing just the song you want is much faster|z. B. 1:30 – nur das gewünschte Stück geht viel schneller
请填写结束时间。|Please enter an end time.|Bitte eine Endzeit eingeben.
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
请至少勾选一个声部。|Please tick at least one part.|Bitte mindestens eine Stimme auswählen.
Hugging Face 授权|Hugging Face access|Hugging-Face-Zugang
打开网页|Open web page|Webseite öffnen
令牌一般以 hf_ 开头，请检查一下。|Tokens usually start with hf_ – please check.|Tokens beginnen meist mit hf_ – bitte prüfen.
声部音量地图已生成：颜色越深越响；最上面一行是每一秒最响的声部|Map ready: darker = louder; the top row shows the loudest part each second|Karte fertig: dunkler = lauter; oben die jeweils lauteste Stimme
请先在“演唱会降噪”页安装 AI 组件（扒谱和它共用显卡环境）。|Please install the AI on the “Concert cleanup” tab first (shared GPU environment).|Bitte zuerst im Tab „Konzert-Bereinigung“ die KI installieren (gemeinsame GPU-Umgebung).
最响|loudest|lauteste
设置|Settings|Einstellungen
界面与更新|Interface & updates|Oberfläche & Updates
AI 组件|AI components|KI-Komponenten
处理|Processing|Verarbeitung
中间文件位置|Temporary files|Temporäre Dateien
安装 / 重新安装|Install / reinstall|Installieren / neu
安装进度会显示在对应的功能页里|Progress is shown on the matching feature page|Der Fortschritt erscheint auf der jeweiligen Seite
留空 = 系统临时文件夹（一般在 C 盘）；整场演唱会每小时约 3 GB，旧的会自动删掉|Empty = system temp folder (usually C:); ≈3 GB per concert hour, old files are removed automatically|Leer = System-Temp-Ordner (meist C:); ≈3 GB pro Konzertstunde, alte Dateien werden automatisch gelöscht
已保存|Saved|Gespeichert
已安装|Installed|Installiert
未安装（约 5 GB）|Not installed (≈5 GB)|Nicht installiert (≈5 GB)
未安装（约 300 MB + 模型 1.4 GB）|Not installed (≈300 MB + 1.4 GB model)|Nicht installiert (≈300 MB + 1,4 GB Modell)
授权已设置|access set|Zugang gesetzt
需要 Hugging Face 授权|Hugging Face access needed|Hugging-Face-Zugang nötig
未安装（约 26 MB）|Not installed (≈26 MB)|Nicht installiert (≈26 MB)
已就绪|Ready|Bereit
第一次录音时自动准备|Prepared automatically on first recording|Wird bei der ersten Aufnahme eingerichtet
录音组件|Recorder|Rekorder
● 有新版本|● Update available|● Update verfügbar
扒谱与练琴|Transcribe & practise|Transkribieren & Üben
创作|Create|Komponieren
修音|Clean up|Bearbeiten
获取声音|Get audio|Audio holen
灵感本|Idea book|Ideenbuch
和弦与音阶|Chords & scales|Akkorde & Skalen
练第|Practise bars|Takte üben
小节|bars|Takte
渐进提速|Speed ramp|Tempo steigern
节拍器|Metronome|Metronom
预备拍|Count-in|Einzähler
升降调|Transpose|Transponieren
导出这段|Export section|Abschnitt exportieren
调音器|Tuner|Stimmgerät
跟弹检测|Play-along check|Mitspiel-Check
■ 停止检测|■ Stop check|■ Check stoppen
调音器：对着麦克风弹一根空弦|Tuner: play an open string into the microphone|Stimmgerät: eine leere Saite ins Mikrofon spielen
和弦与音阶|Chords & scales|Akkorde & Skalen
写歌用：选调和调式 → 点调内和弦搭进行（会推荐下一个和弦）→ 试听 → 指板上看音阶和和弦音，写旋律、Solo 时参考|For songwriting: pick key and mode → build a progression from the diatonic chords (with next-chord suggestions) → listen → see the scale and chord tones on the fretboard|Zum Songwriting: Tonart und Modus wählen → Folge aus leitereigenen Akkorden bauen (mit Vorschlägen) → anhören → Skala und Akkordtöne auf dem Griffbrett sehen
调|Key|Tonart
调式|Mode|Modus
七和弦|Seventh chords|Septakkorde
指板显示|Fretboard labels|Griffbrett-Beschriftung
音名|Note names|Tonnamen
级数（1 b3 5…）|Degrees (1 b3 5…)|Stufen (1 b3 5…)
调内和弦：|Diatonic chords:|Leitereigene Akkorde:
推荐下一个：|Next chord:|Nächster Akkord:
常用进行：|Common progressions:|Gängige Folgen:
选一个…|Choose…|Auswählen…
▶ 试听|▶ Listen|▶ Anhören
■ 停|■ Stop|■ Stopp
撤销|Undo|Rückgängig
清空|Clear|Leeren
导出 MIDI|Export MIDI|MIDI exportieren
存进灵感本|Save to idea book|Ins Ideenbuch
点上面的和弦加进进行；点进行里的和弦在指板上看它的和弦音，右键删掉|Click a chord above to add it; click a chord in the progression to see its tones on the fretboard, right-click to remove|Oben einen Akkord anklicken, um ihn hinzuzufügen; in der Folge anklicken, um seine Töne zu sehen, Rechtsklick entfernt
和弦进行会显示在这里（点上面的和弦添加）|Your progression appears here (click chords above)|Hier erscheint die Akkordfolge (oben Akkorde anklicken)
对着麦克风弹一段 riff / 旋律，停下来就自动扒成六线谱和和弦，存进灵感本；以后可以试听、打开谱、拿去吉他页跟练|Play a riff or melody into the microphone; when you stop, it is transcribed to tab and chords and saved here – listen, open the sheet or practise it later|Spiele ein Riff oder eine Melodie ins Mikrofon; nach dem Stoppen wird es als Tab und Akkorde gespeichert – später anhören, Noten öffnen oder üben
● 录一段（麦克风）|● Record (microphone)|● Aufnehmen (Mikrofon)
■ 停止并扒谱|■ Stop & transcribe|■ Stopp & transkribieren
导入音频…|Import audio…|Audio importieren…
打开灵感本文件夹|Open idea folder|Ideenordner öffnen
名称|Name|Name
时间|Date|Datum
速度|Tempo|Tempo
和弦|Chords|Akkorde
标签|Tags|Tags
▶ 播放|▶ Play|▶ Abspielen
打开谱|Open sheet|Noten öffnen
去吉他页跟练|Practise on guitar page|Auf Gitarrenseite üben
改名…|Rename…|Umbenennen…
标签…|Tags…|Tags…
删除|Delete|Löschen
新名字：|New name:|Neuer Name:
扒好了，已存进灵感本|Done – saved to the idea book|Fertig – im Ideenbuch gespeichert
录音太短（不到 2 秒）|Recording too short (under 2 s)|Aufnahme zu kurz (unter 2 s)
这个灵感还没有扒出来的谱（和弦进行可以在“和弦与音阶”页试听）。|This idea has no transcription yet (progressions can be played on the “Chords & scales” page).|Diese Idee hat noch keine Transkription (Akkordfolgen auf der Seite „Akkorde & Skalen“ anhören).
这段音频里只有吉他吗？\n（是：直接扒，快；否：先把吉他从乐队里分离出来）|Is this audio guitar only?\n(Yes: transcribe directly, fast; No: separate the guitar from the band first)|Ist nur Gitarre zu hören?\n(Ja: direkt transkribieren, schnell; Nein: Gitarre zuerst von der Band trennen)
跟弹检测：戴耳机放伴奏（免得麦克风听到原曲），跟着弹；弹对的音变绿，漏掉的变红|Play-along check: use headphones for the backing (so the mic doesn't hear it) and play along; correct notes turn green, missed ones red|Mitspiel-Check: Begleitung über Kopfhörer und mitspielen; richtige Töne werden grün, verpasste rot
正在录音：弹吧！（离麦克风近一点；弹完点“停止并扒谱”）|Recording – play! (stay close to the mic; click “Stop & transcribe” when done)|Aufnahme läuft – spiel! (nah ans Mikrofon; danach „Stopp & transkribieren“)
指法|Fingering|Fingersatz
顺手（推荐）|Comfortable (recommended)|Bequem (empfohlen)
一根弦优先|One string first|Eine Saite zuerst
后摇模式|Post-rock mode|Post-Rock-Modus
调弦|Tuning|Stimmung
自动|Auto|Automatisch
看|Show|Zeigen
全部|All|Alle
例：2:15|e.g. 2:15|z. B. 2:15
打开吉他谱（含指板图）|Open guitar sheet (with fretboards)|Gitarrenblatt öffnen (mit Griffbrettern)
● 边放边录（电脑声音）|● Record while playing (PC audio)|● Beim Abspielen aufnehmen (PC-Ton)
■ 停止并扒谱|■ Stop & transcribe|■ Stopp & transkribieren
正在停止……|Stopping…|Wird gestoppt…
现在有别的任务在进行（扒谱或录音），请等它结束。|Another task (transcription or recording) is running – please wait for it to finish.|Eine andere Aufgabe (Transkription oder Aufnahme) läuft – bitte warten.
需要先在“设置”里安装演唱会降噪组件。|Please install “Concert cleanup” in Settings first.|Bitte zuerst in den Einstellungen „Konzert-Bereinigung“ installieren.
正在录电脑的声音：现在去播放音乐吧。会录下电脑里所有的声音，其他提示音也会被录进去|Recording your PC audio: start the music now. Everything the PC plays is recorded, including notification sounds|PC-Ton wird aufgenommen: jetzt die Musik starten. Alles, was der PC abspielt, wird aufgenommen, auch Hinweistöne
录音太短（不到 3 秒），没有扒谱|Recording too short (under 3 s) – nothing transcribed|Aufnahme zu kurz (unter 3 s) – nichts transkribiert
吉他扒谱|Guitar|Gitarre
吉他扒谱：和弦与 Solo|Guitar: chords & solos|Gitarre: Akkorde & Soli
分离出吉他 → 认和弦（弹唱）→ Solo 扒成六线谱（第几弦第几品）→ 指板上跟着练，可放慢、可只听伴奏|Isolate the guitar → detect chords → transcribe solos as tab (string & fret) → practise on the fretboard, slowed down or with the backing track only|Gitarre isolieren → Akkorde erkennen → Soli als Tabulatur (Saite & Bund) → auf dem Griffbrett üben, verlangsamt oder nur mit Begleitung
选一首民谣 / 摇滚歌曲：认出和弦（带按法图和变调夹建议），把吉他 Solo 扒成六线谱，并在指板上显示按哪里|Pick a folk / rock song: chords with diagrams and a capo suggestion, the guitar solo as tab, and where to press on the fretboard|Wähle einen Folk-/Rock-Song: Akkorde mit Griffbildern und Kapo-Vorschlag, das Gitarrensolo als Tabulatur und wo man greift
和弦（弹唱）|Chords (strumming)|Akkorde (Begleitung)
Solo 六线谱|Solo tab|Solo-Tab
只要旋律（Solo 推荐）|Melody only (best for solos)|Nur Melodie (für Soli)
现场录音：先去观众声|Live recording: remove crowd first|Live-Aufnahme: erst Publikum entfernen
例：2:15；扒 Solo 时只选 Solo 那一段，快很多|e.g. 2:15 – select just the solo, it is much faster|z. B. 2:15 – nur das Solo wählen geht viel schneller
开始扒谱|Start|Start
▶ 播放|▶ Play|▶ Abspielen
⏸ 暂停|⏸ Pause|⏸ Pause
⏮ 从头|⏮ Restart|⏮ Von vorn
速度|Speed|Tempo
听|Listen to|Hören
原曲|Original|Original
伴奏（去掉吉他）|Backing (no guitar)|Begleitung (ohne Gitarre)
只有吉他|Guitar only|Nur Gitarre
循环|Loop|Schleife
请至少勾选“和弦”或“Solo 六线谱”中的一项。|Please select “Chords” or “Solo tab”.|Bitte „Akkorde“ oder „Solo-Tab“ wählen.
和弦会显示在这里：按法图、变调夹建议，播放时当前和弦会变成橙色|Chords appear here with diagrams and a capo suggestion; the current chord turns orange while playing|Hier erscheinen Akkorde mit Griffbildern und Kapo-Vorschlag; der aktuelle Akkord wird beim Abspielen orange
指板：播放时显示现在要按的位置（橙色），下一个音是空心圈；没有 Solo 时显示当前和弦的按法|Fretboard: while playing, orange = press now, hollow = next note; without a solo it shows the current chord shape|Griffbrett: orange = jetzt greifen, hohl = nächster Ton; ohne Solo wird der aktuelle Akkordgriff gezeigt
显卡半精度（快 1.5–2 倍）|GPU half precision (1.5–2× faster)|GPU-Halbpräzision (1,5–2× schneller)
快速分离（快约 1.9 倍，差别约 −38 dB）|Fast separation (≈1.9× faster, difference ≈ −38 dB)|Schnelle Trennung (≈1,9× schneller, Unterschied ≈ −38 dB)
选择安装版本|Choose an edition|Version wählen
安装演唱会降噪组件|Install concert cleanup|Konzert-Bereinigung installieren
同时重装运行环境（组件出错时用）|Also reinstall the runtime (use if something is broken)|Laufzeitumgebung neu installieren (bei Fehlern)
以后需要别的功能时，可以回来升级；勾选没下载的功能时也会自动补下。安装完成后可以离线使用。|You can upgrade later; features that are not downloaded yet are fetched automatically when you tick them. Works offline once installed.|Später erweiterbar; fehlende Funktionen werden beim Ankreuzen automatisch geladen. Danach offline nutzbar.
开始安装|Install|Installieren
按电脑配置推荐|Recommend for this PC|Für diesen PC empfehlen
已按电脑配置选好处理步骤|Steps chosen for this computer|Schritte für diesen Computer gewählt
电脑配置|Computer|Computer
正在检查……|Checking…|Wird geprüft…
重新检测|Re-check|Neu prüfen
解除限制|Unlock|Entsperren
解除后所有选项都能选，但在这台电脑上可能要很久，或者显存不够而失败。确定吗？|All options become available, but on this computer they may take very long or fail for lack of GPU memory. Continue?|Alle Optionen werden freigegeben, können auf diesem Computer aber sehr lange dauern oder mangels Grafikspeicher scheitern. Fortfahren?
链接或文件|Link or file|Link oder Datei
下载与安装进度|Downloads & installs|Downloads & Installation
只能拖入视频或音频文件。|Only video or audio files can be dropped.|Nur Video- oder Audiodateien können abgelegt werden.
请先到左下角“设置”里安装扒谱组件。|Please install transcription under “Settings” (bottom left) first.|Bitte zuerst unter „Einstellungen“ (unten links) die Transkription installieren.
请先在“设置”里安装“演唱会降噪”组件（扒谱和它共用显卡环境）。|Please install “Concert cleanup” in Settings first (transcription shares its GPU environment).|Bitte zuerst in den Einstellungen „Konzert-Bereinigung“ installieren (die Transkription nutzt dieselbe GPU-Umgebung).
现在没有进行中的下载。安装组件或第一次用某个模型时，这里会显示下载进度和速度。|No download in progress. Progress and speed appear here while installing components or fetching a model for the first time.|Kein Download aktiv. Beim Installieren oder ersten Laden eines Modells erscheinen hier Fortschritt und Geschwindigkeit.
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
版本 |Version |Version
渐进提速：这一遍 |Speed ramp: this pass |Tempo steigern: dieser Durchgang 
伴奏升 |Backing up |Begleitung + 
伴奏降 |Backing down |Begleitung − 
 个半音：夹变调夹第 | semitones: capo on fret | Halbtöne: Kapo im Bund 
 品，照谱上的指法弹| and play the shapes as written|, Griffe wie notiert spielen
 个半音：把吉他整体调低 | semitones: tune the whole guitar down | Halbtöne: Gitarre um 
 个半音，照谱上的指法弹| semitones and play as written| Halbtöne tiefer stimmen, wie notiert spielen
已导出：|Exported: |Exportiert: 
 · 跟弹 | · play-along | · Mitspielen 
 · 跟弹检测中| · checking…| · Check läuft
准了|in tune|gestimmt
调高一点|tune up|höher stimmen
调低一点|tune down|tiefer stimmen
 音分| cents| Cent
最接近：|Closest: |Am nächsten: 
 弦（| string (| Saite (
：黑 = 主音，白 = 音阶里的音|: black = root, white = scale notes|: schwarz = Grundton, weiß = Skalentöne
，橙 = |, orange = |, orange = 
 的和弦音| chord tones| Akkordtöne
已存进灵感本：|Saved to the idea book: |Im Ideenbuch gespeichert: 
正在录音：|Recording: |Aufnahme: 
扒谱失败：|Transcription failed: |Transkription fehlgeschlagen: 
麦克风：|Microphone: |Mikrofon: 
正在安装 |Installing |Installiere 
（很小，一次就好）……| (small, once only)…| (klein, nur einmal)…
大调（自然大调）|Major (Ionian)|Dur (ionisch)
小调（自然小调）|Minor (Aeolian)|Moll (äolisch)
大调五声|Major pentatonic|Dur-Pentatonik
小调五声|Minor pentatonic|Moll-Pentatonik
布鲁斯|Blues|Blues
和声小调|Harmonic minor|Harmonisch Moll
旋律小调|Melodic minor|Melodisch Moll
录音音量很小（峰值 |Very quiet recording (peak |Sehr leise Aufnahme (Spitze 
 dB），已先放大到正常音量再识别。| dB) – boosted to a normal level before analysis.| dB) – vor der Analyse auf normalen Pegel angehoben.
后摇模式：把安静段落的音量拉平|Post-rock: levelling quiet passages|Post-Rock: leise Passagen angleichen
后摇模式：去掉延音回声、识别震音、分开两把吉他|Post-rock: removing delay echoes, tremolo picking, splitting two guitars|Post-Rock: Delay-Echos entfernen, Tremolo erkennen, zwei Gitarren trennen
从“其他”声部里找吉他|Looking for guitar in the “other” stem|Gitarre in der „Rest“-Spur suchen
检测到延音效果（约 |Delay effect detected (about |Delay erkannt (ca. 
 ms），已去掉回声音符。| ms) – echo notes removed.| ms) – Echo-Töne entfernt.
在“其他”声部里又找到 |Found |In der „Rest“-Spur 
 个吉他音（混响很重的那把吉他常被分到这里）。| more guitar notes in the “other” stem (heavily reverbed guitars often land there).| weitere Gitarrentöne gefunden (stark verhallte Gitarren landen oft dort).
建议调弦：|Suggested tuning: |Empfohlene Stimmung: 
（按这个调弦排的指法）| (the tab uses this tuning)| (die Tabulatur nutzt diese Stimmung)
两把吉他（|Two guitars (|Zwei Gitarren (
按左右声道分开|split by left/right|nach links/rechts getrennt
按音色分开|split by tone|nach Klang getrennt
按声部分开（高的旋律为吉他 1）|split by part (higher melody = guitar 1)|nach Stimmen getrennt (höhere Melodie = Gitarre 1)
按声部分开（混响很重的那把单独一把）|split by part (the heavily reverbed one separately)|nach Stimmen getrennt (die stark verhallte separat)
）：|): |): 
建议调弦 |suggested tuning |empfohlene Stimmung 
吉他 1（主奏）|Guitar 1 (lead)|Gitarre 1 (Lead)
吉他 2|Guitar 2|Gitarre 2
只有吉他 1|Guitar 1 only|Nur Gitarre 1
只有吉他 2|Guitar 2 only|Nur Gitarre 2
橙=吉他1 绿=吉他2|orange = guitar 1, green = guitar 2|orange = Gitarre 1, grün = Gitarre 2
识别滑音、推弦、击勾弦|Detecting slides, bends, hammer-ons/pull-offs|Slides, Bendings, Hammer-ons/Pull-offs erkennen
击弦 h|hammer-on h|Hammer-on h
勾弦 p|pull-off p|Pull-off p
推弦 ↑|bend ↑|Bending ↑
 再放回| & release| & zurück
揉弦 ~|vibrato ~|Vibrato ~
滑|slide|Slide
演奏技巧识别失败（|Technique detection failed (|Technik-Erkennung fehlgeschlagen (
），只标音符。|) – notes only.|) – nur Töne.
● 正在边放边录 · 已录 |● Recording while playing · |● Aufnahme läuft · 
 · 停止后自动完整扒谱（和弦 + Solo 六线谱）| recorded · full transcription (chords + solo tab) after you stop| aufgenommen · vollständige Transkription (Akkorde + Solo-Tab) nach dem Stoppen
刚才的和弦：|Recent chords:|Letzte Akkorde:
（还没有）|(none yet)|(noch keine)
目前看来：夹变调夹第 |So far: a capo on fret |Bisher: Kapo im Bund 
 品会更好按（停止后按完整结果给出）| would be easier (final advice after you stop)| wäre leichter (endgültig nach dem Stoppen)
实时和弦：|Live chord: |Live-Akkord: 
录好了（|Recorded (|Aufgenommen (
），开始完整扒谱……|), starting the full transcription…|), vollständige Transkription startet…
实时和弦出错：|Live chords failed: |Live-Akkorde fehlgeschlagen: 
变调夹第 |Capo on fret |Kapo im Bund 
 品（按下面的指法弹）| (play the shapes below)| (Griffe unten spielen)
不用变调夹|no capo|kein Kapo
当前和弦：|Current chord: |Aktueller Akkord: 
（变调夹 | (capo | (Kapo 
 品）| )| )
整首偏|whole song is |ganzes Lied 
 音分| cents| Cent
 个和弦| chords| Akkorde
，建议变调夹第 |, suggested capo: fret |, Kapo-Vorschlag: Bund 
Solo |Solo |Solo 
 个音| notes| Töne
点“播放”在指板上跟着练；文件已存到输出文件夹|Press “Play” to practise on the fretboard; files saved to the output folder|„Abspielen“ drücken und auf dem Griffbrett üben; Dateien im Ausgabeordner
分离吉他、贝斯和其他声部|Separating guitar, bass and other parts|Gitarre, Bass und Rest trennen
找拍子和小节线|Finding beats and bars|Takt und Schläge finden
识别和弦|Detecting chords|Akkorde erkennen
扒吉他音符|Transcribing guitar notes|Gitarrentöne transkribieren
排六线谱把位|Choosing tab positions|Tab-Positionen wählen
加载扒谱模型|Loading the transcription model|Transkriptionsmodell wird geladen
正在补装 Guitar Pro 文件组件（约 1 MB）……|Installing the Guitar Pro file component (about 1 MB)…|Guitar-Pro-Komponente wird installiert (ca. 1 MB)…
已保存到：|Saved to: |Gespeichert in: 
大调| major| Dur
小调| minor| Moll
 · 还要约 | · about | · noch ca. 
去观众声 + 去底噪；适合没有 NVIDIA 显卡的电脑|crowd + noise removal; for PCs without an NVIDIA GPU|Publikum + Rauschen entfernen; für PCs ohne NVIDIA-GPU
再加人声/伴奏分离、音质修复；适合 4–8 GB 显存|adds vocal/backing split and restoration; for 4–8 GB VRAM|plus Gesang/Begleitung-Trennung und Restaurierung; für 4–8 GB VRAM
再加去混响（现场感）；适合 8 GB 以上显存|adds de-reverb (room control); for 8 GB+ VRAM|plus Enthallung (Raumregler); ab 8 GB VRAM
没有可用的 NVIDIA 显卡，用不上人声分离和音质修复|no usable NVIDIA GPU – vocal split and restoration would be too slow|keine nutzbare NVIDIA-GPU – Trennung und Restaurierung wären zu langsam
没有可用的 NVIDIA 显卡|no usable NVIDIA GPU|keine nutzbare NVIDIA-GPU
未安装（按电脑配置选版本，约 2–7 GB）|Not installed (edition chosen by your PC, about 2–7 GB)|Nicht installiert (Version je nach PC, ca. 2–7 GB)
需下载约 |download about |Download ca. 
无需下载|nothing to download|kein Download
精简版|Lite|Lite
标准版|Standard|Standard
完整版|Full|Vollständig
已安装|installed|installiert
推荐|recommended|empfohlen
推荐组合：|Recommended: |Empfohlen: 
显卡够强：全部打开（去混响后可以调现场感）；显卡半精度加速|strong GPU: everything on (de-reverb enables the room slider); half-precision GPU|starke GPU: alles an (Enthallung ermöglicht den Raumregler); GPU-Halbpräzision
显存较小：不做去混响（多一个大模型、最占显存），其余全开；一次只放一个模型|little GPU memory: no de-reverb (one more large model), everything else on; one model at a time|wenig Grafikspeicher: keine Enthallung (ein weiteres großes Modell), sonst alles an; ein Modell zur Zeit
没有可用的显卡：只去观众声和底噪（最影响听感的两项），其余在 CPU 上太慢|no usable GPU: only crowd and noise removal (the two that matter most), the rest is too slow on CPU|keine nutzbare GPU: nur Publikum und Rauschen entfernen (am wichtigsten), der Rest ist auf der CPU zu langsam
去观众声|remove crowd|Publikum entfernen
去底噪|remove noise|Rauschen entfernen
分离人声和伴奏|split vocals/backing|Gesang/Begleitung trennen
高配（显卡加速，全部可用）|High-end (GPU, everything available)|Stark (GPU, alles verfügbar)
中配（显卡加速，显存较小）|Mid-range (GPU with little memory)|Mittel (GPU mit wenig Speicher)
低配（没有可用的 NVIDIA 显卡，用 CPU）|Low-end (no usable NVIDIA GPU, CPU only)|Schwach (keine nutzbare NVIDIA-GPU, nur CPU)
按你的电脑配置，预计需要|Estimated time on this computer: |Geschätzte Dauer auf diesem Computer: 
想快一点：少勾几项处理，或扒谱选“快速（中模型）”。|To speed up: select fewer steps, or choose “Fast (medium model)” for transcription.|Schneller: weniger Schritte wählen oder bei der Transkription „Schnell (mittleres Modell)“.
现在开始吗？|Start now?|Jetzt starten?
按你的电脑配置，已关闭：|Turned off for this computer: |Für diesen Computer deaktiviert: 
（可在“设置”里解除限制）| (can be unlocked in Settings)| (in den Einstellungen entsperrbar)
（按你的电脑配置估算）| (estimate for this computer)| (Schätzung für diesen Computer)
已解除限制：所有选项都能选，但可能很慢或显存不够|Unlocked: everything available, but may be slow or run out of GPU memory|Entsperrt: alles verfügbar, kann aber langsam sein oder zu wenig Grafikspeicher haben
预计需要|Estimated |Geschätzt 
（不能用于 AI 加速）| (not usable for AI)| (nicht für KI nutzbar)
没有独立显卡|no dedicated GPU|keine dedizierte GPU
已锁定：|Locked: |Gesperrt: 
扒谱大模型|large transcription model|großes Transkriptionsmodell
束搜索|beam search|Strahlsuche
声部音量地图|parts loudness map|Stimmen-Lautstärkekarte
音质修复|restoration|Restaurierung
去混响|de-reverb|Enthallung
显卡：|GPU: |GPU: 
内存：|RAM: |RAM: 
（显存 | (VRAM | (VRAM 
 线程）| threads)| Threads)
预计|est. |ca. 
约 |about |ca. 
 分钟| min| Min.
 小时| h| Std.
最近 2 分钟下载速度 · 最高 |Download speed, last 2 min · peak |Download-Tempo, letzte 2 Min. · max. 
⬇ 下载中|⬇ Downloading|⬇ Lädt
 · 剩余约 | · about | · noch ca. 
等待数据…|waiting for data…|warte auf Daten…
准备中…|Preparing…|Vorbereitung…
下载模型|Downloading model|Modell wird geladen
最近一次|Last run|Zuletzt
已结束|finished|beendet
 下载完成| downloaded| geladen
模型下载完成|Model downloaded|Modell geladen
模型下载中断|Model download interrupted|Modell-Download abgebrochen
安装失败|Installation failed|Installation fehlgeschlagen
安装扒谱组件|Install transcription|Transkription installieren
安装演唱会降噪组件|Installing concert cleanup|Konzert-Bereinigung wird installiert
安装 AI 人声增强|Installing AI voice enhance|KI-Sprachverbesserung wird installiert
扒谱组件安装完成|Transcription installed|Transkription installiert
演唱会降噪组件安装完成|Concert cleanup installed|Konzert-Bereinigung installiert
AI 人声增强安装完成|AI voice enhance installed|KI-Sprachverbesserung installiert
扒谱模型|transcription model|Transkriptionsmodell
节拍模型|beat model|Beat-Modell
六轨分离模型|6-stem model|6-Spur-Modell
音质修复模型|restoration model|Restaurierungsmodell
已选择本地文件：|Local file selected: |Lokale Datei gewählt: 
点“开始提取”就保存成上面选的音频格式（不用联网）。|Click “Start” to save it in the format above (no internet needed).|„Start“ klicken, um im obigen Format zu speichern (ohne Internet).
已选择：|Selected: |Ausgewählt: 
本地文件：|Local file: |Lokale Datei: 
 小时 | h | Std. 
 分 | min | Min. 
 秒| s| s
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
    for zh, western in (("：", ": "), ("，", ", "), ("、", ", "), ("；", "; "), ("（", " ("), ("）", ")"), ("。", ". "), ("“", "“"), ("…", "…")):
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
