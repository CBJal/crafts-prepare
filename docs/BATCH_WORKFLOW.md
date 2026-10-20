# Prepare a batch

1. Open **Import folder** and choose the folder containing the original clips and their adjacent XML files. Browse shows real thumbnails, filenames and detected profiles. Keep the XML next to its corresponding video.
2. Select the clips with Ctrl/Shift, or use Ctrl+A. Open **View** to inspect the focused clip while retaining the selection.
3. In **Color**, leave Input profile on **Auto** for supported camera metadata. Choose a **Look LUT** for a creative look and click **Apply to selected**. Use Input LUT instead when your LUT performs the camera conversion. Enable “Look includes camera” only when that look already includes the camera transform.
4. In **Gyro**, enable **Stabilize with embedded gyro**, choose strength/field of view/horizon lock, and click **Apply to selected**. Each clip keeps its own colour settings. Numeric motion must exist in the original video; an XML filename alone does not prove it.
5. Click **Update preview** for the focused clip's actual stabilization. Use 2-up or Wipe, Play, scrub or the frame buttons to compare. If gyro settings change, the previous geometry is labeled stale until updated. Preview rendering does not publish an export.
6. Click **Export**, choose a different destination folder and a suffix, select a format and **Source size** or Resize, then **Export selected**. ProRes 422 retains 10-bit footage; an 8-bit preset requires acknowledgement when converting bit depth. Every selected clip gets one combined preparation job.
7. Inspect each job's success/error in the Export dialog. Cancel stops the selected batch; Retry failed retries failures without overwriting completed output. Whole clips export by default; saved trim is an optional advanced choice.

Single-clip changes stay on that clip. Color Apply copies colour/LUT controls; Gyro Apply copies stabilization controls. Auto resolves each selected clip's own profile, including mixed HLG and S-Log3 batches. Original media is checked by checksum and is not overwritten.

If an XML file changes or returns after being missing, select its clip and use **Meta → Refresh metadata**. Existing correction settings stay. Use **Save** to keep the project; a missing LUT can be chosen again or cleared. A failed edit is kept until repaired or explicitly discarded.

Install FFmpeg/ffprobe and Gyroflow separately and select their executable paths under **Tools** if needed. Installed use needs no Python, Node.js, or browser. The application is independently developed; it does not implement every Sony-specific correction.

Choose **Tools → Appearance → Light / Dark / System** to change the interface. The choice is kept for the next launch. Export jobs show their actual percentage; **Cancel batch** cancels the current batch.
