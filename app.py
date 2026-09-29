import os, subprocess, tempfile
import streamlit as st

MAX_SEC = 600  # 10 minutes


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(r.stderr[-800:])


def dur(path):
    o = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return float(o.strip())


def to_sec(t):
    s = 0.0
    for x in t.strip().split(":"):
        s = s * 60 + float(x)
    return s


def whisper_times(audio, paragraphs, total):
    from faster_whisper import WhisperModel
    model = WhisperModel("base.en", compute_type="int8")
    segs, _ = model.transcribe(audio, language="en", word_timestamps=True)
    words = [w for s in segs for w in s.words]
    n = len(paragraphs)
    if not words:
        return equal_times(n, total)
    counts = [max(1, len(p.split())) for p in paragraphs]
    tot, acc, bounds = sum(counts), 0, [0.0]
    for c in counts[:-1]:
        acc += c
        bounds.append(words[min(int(acc / tot * len(words)), len(words) - 1)].start)
    bounds.append(total)
    return list(zip(bounds[:-1], bounds[1:]))


def equal_times(n, total):
    step = total / n
    return [(i * step, (i + 1) * step) for i in range(n)]


st.title("Auto Video Editor")
audio_f = st.file_uploader("Audio (English)", type=["mp3", "wav", "m4a"])
imgs = st.file_uploader("Images", type=["jpg", "jpeg", "png"],
                        accept_multiple_files=True)
mode = st.radio("Timing", ["Auto from script", "Manual timing", "Equal split"])
remove_sil = st.checkbox("Remove silent parts", True)
thr = st.slider("Silence threshold (dB)", -60, -20, -40)
min_sil = st.slider("Remove silence longer than (sec)", 0.2, 2.0, 0.5)
fmt = st.radio("Format", ["16:9 (YouTube)", "9:16 (Shorts/Reels)"])

script = ""
if mode == "Auto from script":
    script = st.text_area("Script: one paragraph per scene (blank line between). "
                          "Scene 1 = first image, scene 2 = second, ... "
                          "(images are sorted by file name)", height=250)
elif mode == "Manual timing":
    script = st.text_area(
        "One line per scene: start-end | filename\n"
        "Example: 0:00-0:12 | scene1.jpg  (times are on the audio AFTER silence removal)",
        height=250)

if st.button("Make video") and audio_f and imgs:
    try:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "in" + os.path.splitext(audio_f.name)[1])
            open(src, "wb").write(audio_f.getvalue())
            audio = os.path.join(tmp, "clean.m4a")
            if remove_sil:
                flt = (f"silenceremove=start_periods=1:start_threshold={thr}dB:"
                       f"stop_periods=-1:stop_duration={min_sil}:"
                       f"stop_threshold={thr}dB:stop_silence=0.15")
                run(["ffmpeg", "-y", "-i", src, "-af", flt, "-c:a", "aac",
                     "-b:a", "192k", audio])
            else:
                run(["ffmpeg", "-y", "-i", src, "-c:a", "aac", "-b:a", "192k", audio])
            total = dur(audio)
            st.write(f"Audio length after cleaning: {total/60:.1f} min")
            if total > MAX_SEC:
                st.error("Audio is longer than 10 minutes. Please shorten it.")
                st.stop()

            # save images
            files = sorted(imgs, key=lambda f: f.name)
            paths = {}
            for i, f in enumerate(files):
                p = os.path.join(tmp, f"img_{i}" + os.path.splitext(f.name)[1])
                open(p, "wb").write(f.getvalue())
                paths[f.name] = p
            ordered = [paths[f.name] for f in files]

            # timing
            if mode == "Manual timing":
                scenes = []
                for line in script.strip().splitlines():
                    t, name = line.split("|")
                    a, b = t.split("-")
                    scenes.append((paths[name.strip()], to_sec(b) - to_sec(a)))
            else:
                if mode == "Auto from script":
                    paras = [p for p in script.split("\n\n") if p.strip()]
                    if len(paras) != len(ordered):
                        st.error(f"{len(paras)} paragraphs but {len(ordered)} images. "
                                 "They must match.")
                        st.stop()
                    with st.spinner("Listening to audio (Whisper)..."):
                        times = whisper_times(audio, paras, total)
                else:
                    times = equal_times(len(ordered), total)
                scenes = [(p, b - a) for p, (a, b) in zip(ordered, times)]

            used = sum(d for _, d in scenes)
            if used < total:  # last image stays till audio ends
                scenes[-1] = (scenes[-1][0], scenes[-1][1] + total - used)

            lst = os.path.join(tmp, "list.txt")
            with open(lst, "w") as f:
                for p, d in scenes:
                    f.write(f"file '{p}'\nduration {d:.3f}\n")
                f.write(f"file '{scenes[-1][0]}'\n")

            w, h = (1280, 720) if fmt.startswith("16") else (720, 1280)
            vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                  f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,format=yuv420p")
            out = os.path.join(tmp, "out.mp4")
            with st.spinner("Rendering video..."):
                run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                     "-i", audio, "-vf", vf, "-r", "25", "-c:v", "libx264",
                     "-preset", "veryfast", "-c:a", "aac", "-shortest", out])
            data = open(out, "rb").read()
        st.success("Done!")
        st.video(data)
        st.download_button("Download MP4", data, "video.mp4", "video/mp4")
    except Exception as e:
        st.error(f"Error: {e}")
