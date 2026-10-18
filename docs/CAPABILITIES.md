# Capabilities and limits

Status below is what this repository implements. "Verified here" means an automated test in this session, on Linux, with the system ffmpeg. It is not a Windows UI test and not a Sony camera test.

| Feature | Status | Verified here |
| --- | --- | --- |
| Loopback bind and Host/Origin checks | Implemented | Yes, API test |
| Import file or folder by path | Implemented | Yes |
| Skip symlinks that resolve outside the selected folder | Implemented | Yes |
| Support decided by ffprobe, not the extension | Implemented | Yes, junk `.mp4` rejected; H.264, HEVC, ProRes, DNxHR probed |
| Canonical path, SHA-256 identity, dedup | Implemented | Yes |
| Source file left unchanged | Implemented | Yes, SHA-256 before and after |
| Metadata: video, audio, data, rate, duration, color, timecode tags | Implemented | Yes for the synthetic fixtures |
| "No gyro" inferred from missing ffprobe tags | Not done | The UI says missing data streams are not that claim |
| Browser preview for H.264 yuv420p | Implemented | Linux Chrome smoke: import, metadata, play, trim 0–1s, H.264 export succeeded. Output probed as 1.000s, 640×360, yuv420p, 25/1, AAC. Windows UI was not driven |
| Proxy or still for other codecs | Implemented | API; stills use ffmpeg |
| In/out trim in seconds, rational frame display | Implemented | Export duration checked within one frame |
| Project JSON with OK/KEEP/NG, notes, markers, queue | Implemented | Save/reload test |
| H.264 MP4 and ProRes 422 MOV export | Implemented when those encoders exist | Yes on this ffmpeg: trimmed/resized H.264 and trimmed ProRes, then decoded |
| HEVC Main, HEVC Main10, DNxHR HQ, DNxHR HQX | Offered only if the encoder exists | HEVC 10-bit source was converted to H.264 only after acknowledgement. DNxHR HQ was probed. A DNxHR export preset exists and was not the required export pair |
| Explicit bit-depth declaration | Implemented | 10-bit to 8-bit is rejected until acknowledged |
| Rational output frame rate | Implemented | 25/1 and 30000/1001 |
| Resize, crop-to-fill, letterbox, rotate | Implemented | Resize covered by the H.264 export test |
| Exposure and saturation via `eq` | Implemented | Same filter string for the still and the export |
| `.cube` LUT via `lut3d` | Implemented on the same filter builder | Not given a separate pixel-diff test |
| HDR display matching | Not implemented | |
| Sony color transforms | Not implemented | |
| vidstab two-pass | Implemented when the filters exist | Synthetic shake: white-square centroid spread dropped. That is a motion measurement, not a quality grade |
| deshake fallback | Implemented and labeled as visual fallback | Export ran. Stabilization quality was not scored |
| Gyroflow CLI adapter | Implemented | Test double only. No installed Gyroflow, so no real gyro render is claimed |
| `.gcsv` parser | Implemented | Units, negative samples, column order, gaps |
| telemetry-parser 0.2.6 helper | Optional, builds with the pinned crates | Synthetic MP4 returned unsupported. No camera claim |
| Queue cancel, retry, collision, failed ffmpeg | Implemented | Yes |
| Windows process-tree kill | Code path uses `taskkill /F /T` | Not executed on Windows. Linux process-group kill was tested |
| Accounts, payments, cloud upload | Not implemented | |

Preset pixel formats:

- H.264 MP4: `yuv420p`, 8-bit
- ProRes 422 MOV: `yuv422p10le`, 10-bit, `prores_ks` profile 2
- HEVC Main: `yuv420p`, 8-bit
- HEVC Main10: `yuv420p10le`, 10-bit
- DNxHR HQ: `yuv422p`, 8-bit, profile `dnxhr_hq`
- DNxHR HQX: `yuv422p10le`, 10-bit, profile `dnxhr_hqx`
