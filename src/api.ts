const API_BASE = "http://127.0.0.1:8000";

export async function analyzeImage(
  file: File,
  packWidthCm?: number,
  packHeightCm?: number,
  isMolded?: boolean
) {
  const formData = new FormData();
  formData.append("file", file);
  if (packWidthCm) formData.append("manual_pack_width_cm", String(packWidthCm));
  if (packHeightCm) formData.append("manual_pack_height_cm", String(packHeightCm));
  if (isMolded !== undefined) formData.append("is_molded", String(isMolded));

  const res = await fetch(`${API_BASE}/analyze/image`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Analysis failed" }));
    throw new Error(err.detail || `Analysis failed: ${res.status}`);
  }
  return res.json();
}

export async function analyzeEcommerce(url: string) {
  const res = await fetch(`${API_BASE}/analyze/ecommerce`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Analysis failed" }));
    throw new Error(err.detail || `Analysis failed: ${res.status}`);
  }
  return res.json();
}

export async function generateReport(
  file: File,
  packWidthCm?: number,
  packHeightCm?: number,
  isMolded?: boolean
) {
  const formData = new FormData();
  formData.append("file", file);
  if (packWidthCm) formData.append("manual_pack_width_cm", String(packWidthCm));
  if (packHeightCm) formData.append("manual_pack_height_cm", String(packHeightCm));
  if (isMolded !== undefined) formData.append("is_molded", String(isMolded));

  const res = await fetch(`${API_BASE}/generate-report`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Report generation failed" }));
    throw new Error(err.detail || `Report generation failed: ${res.status}`);
  }
  return res.blob(); // it's a PDF file
}

export function downloadBlob(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}
