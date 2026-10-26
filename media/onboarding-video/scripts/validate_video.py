"""Decode the exported video and save its technical evidence and contact sheet."""
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path


def main():
    video = Path(sys.argv[1]).resolve()
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-count_frames", "-show_streams", "-show_format",
        "-of", "json", str(video),
    ]))
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert stream["codec_name"] == "h264"
    assert (stream["width"], stream["height"]) == (720, 1280)
    assert stream["r_frame_rate"] == "30/1"
    assert stream["nb_read_frames"] == "900"
    assert stream["pix_fmt"] == "yuv420p"
    assert abs(float(probe["format"]["duration"]) - 30) < 0.01
    assert not any(s["codec_type"] == "audio" for s in probe["streams"])
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(video),
                    "-f", "null", "-"], check=True)
    boxes = []
    with video.open("rb") as handle:
        while header := handle.read(8):
            size, kind = struct.unpack(">I4s", header)
            header_size = 8
            if size == 1:
                size = struct.unpack(">Q", handle.read(8))[0]
                header_size = 16
            boxes.append(kind.decode("ascii"))
            if not size:
                break
            handle.seek(size - header_size, 1)
    assert boxes.index("moov") < boxes.index("mdat"), "faststart required"
    frames = [45, 120, 180, 280, 354, 410, 478, 535, 610, 700, 770, 860]
    selection = "+".join(f"eq(n\\,{frame})" for frame in frames)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-i", str(video),
        "-vf", f"select='{selection}',scale=360:640,tile=3x4",
        "-frames:v", "1", str(video.parent / "storyboard.jpg"),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-ss", "1.5", "-i", str(video),
        "-frames:v", "1", str(video.parent / "poster.jpg"),
    ], check=True)
    report = {
        "file": video.name,
        "sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
        "bytes": video.stat().st_size,
        "duration_seconds": float(probe["format"]["duration"]),
        "frames": int(stream["nb_read_frames"]),
        "codec": stream["codec_name"],
        "resolution": [stream["width"], stream["height"]],
        "fps": stream["r_frame_rate"],
        "pixel_format": stream["pix_fmt"],
        "color_space": stream.get("color_space"),
        "silent": True,
        "faststart": True,
        "full_decode": "passed",
        "review_frames": frames,
    }
    (video.parent / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
