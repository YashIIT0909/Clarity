export const MAX_UPLOAD_BYTES = 10 * 1024 * 1024; // the backend rejects anything larger before compressing
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
const TARGET_BYTES = 500 * 1024; // SerpApi's upload limit

/** Canvas -> JPEG under 500 KB: lower quality first, then shrink 25% at a time. Returns the original file if the
 *  browser can't decode or encode it; the backend then compresses it itself. */
export async function compressForUpload(file: Blob): Promise<Blob> {
  if (file.size <= TARGET_BYTES && file.type === "image/jpeg") return file;
  try {
    const bitmap = await createImageBitmap(file);
    let scale = Math.min(1, 2000 / Math.max(bitmap.width, bitmap.height));
    for (let attempt = 0; attempt < 8; attempt++) {
      const canvas = document.createElement("canvas");
      canvas.width = Math.round(bitmap.width * scale);
      canvas.height = Math.round(bitmap.height * scale);
      const ctx = canvas.getContext("2d");
      if (!ctx) break;
      ctx.fillStyle = "#fff"; // JPEG has no alpha: flatten transparent screenshots onto white
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
      for (const quality of [0.85, 0.7, 0.55]) {
        const blob = await new Promise<Blob | null>((r) => canvas.toBlob(r, "image/jpeg", quality));
        if (blob && blob.size < TARGET_BYTES) return blob;
      }
      scale *= 0.75;
    }
  } catch {
    // fall through: send the original
  }
  return file;
}
