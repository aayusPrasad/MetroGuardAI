import type { Scan } from './mockData';
import { saveFile } from './fileStore';

const API_BASE = "http://127.0.0.1:8000";

export async function analyzeImage(
  file: File,
  packWidthCm?: number,
  packHeightCm?: number,
  isMolded?: boolean
): Promise<Scan> {
  const formData = new FormData();
  formData.append("file", file);
  if (packWidthCm) formData.append("manual_pack_width_cm", String(packWidthCm));
  if (packHeightCm) formData.append("manual_pack_height_cm", String(packHeightCm));
  if (isMolded !== undefined) formData.append("is_molded", String(isMolded));

  const res = await fetch(`${API_BASE}/analyze/image`, { method: "POST", body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Analysis failed" }));
    throw new Error(err.detail || `Analysis failed: ${res.status}`);
  }
  const raw = await res.json();
  const scan = normalizeApiScan(raw, file.name, file);
  await saveFile(scan.id, file);
  return scan;
}

export interface BatchItem {
  file: File;
  status: 'pending' | 'processing' | 'done' | 'failed';
  scan?: Scan;
  error?: string;
}

export async function analyzeBatch(
  files: File[],
  onProgress: (index: number, item: BatchItem) => void,
  packWidthCm?: number,
  packHeightCm?: number,
  isMolded?: boolean
): Promise<BatchItem[]> {
  const items: BatchItem[] = files.map(file => ({ file, status: 'pending' }));

  for (let i = 0; i < files.length; i++) {
    items[i].status = 'processing';
    onProgress(i, items[i]);
    try {
      const scan = await analyzeImage(files[i], packWidthCm, packHeightCm, isMolded);
      items[i].status = 'done';
      items[i].scan = scan;
    } catch (e) {
      items[i].status = 'failed';
      items[i].error = e instanceof Error ? e.message : 'Scan failed';
    }
    onProgress(i, items[i]);
  }

  return items;
}

export async function analyzeEcommerce(url: string): Promise<Scan> {
  const res = await fetch(`${API_BASE}/analyze/ecommerce`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Analysis failed" }));
    throw new Error(err.detail || `Analysis failed: ${res.status}`);
  }
  const raw = await res.json();
  return normalizeApiScan(raw, url);
}

export async function generateReport(
  file: File,
  format: 'pdf' | 'docx' = 'pdf',
  packWidthCm?: number,
  packHeightCm?: number,
  isMolded?: boolean
): Promise<Blob> {
  const formData = new FormData();
  formData.append("file", file);
  formData.append("format", format);
  if (packWidthCm) formData.append("manual_pack_width_cm", String(packWidthCm));
  if (packHeightCm) formData.append("manual_pack_height_cm", String(packHeightCm));
  if (isMolded !== undefined) formData.append("is_molded", String(isMolded));

  const res = await fetch(`${API_BASE}/generate-report`, { method: "POST", body: formData });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Report generation failed" }));
    const message = typeof err.detail === 'string' ? err.detail
      : Array.isArray(err.detail) ? err.detail.map((e: any) => e.msg || JSON.stringify(e)).join('; ')
      : `Report generation failed: ${res.status}`;
    throw new Error(message);
  }
  return res.blob();
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

/**
 * Bridges real backend responses onto the frontend's Scan type.
 * Handles two known gaps between backend output and mockData's Scan shape:
 *  1. Backend has no persistence yet — no id/product_name/manufacturer/
 *     scan_date exist in the raw response, so we synthesize them here.
 *  2. Backend's field/value numeric types come through as strings from
 *     regex extraction (e.g. mrp.amount: "45.00"), while Scan's FieldValue
 *     type expects `amount: number` — coerce these to numbers.
 */
function normalizeApiScan(raw: any, sourceLabel: string, sourceFile?: File): Scan {
  const fields: Scan['fields'] = {};
  for (const [key, field] of Object.entries<any>(raw.fields || {})) {
    let value = field.value;
    if (value && typeof value === 'object' && !Array.isArray(value) && 'amount' in value) {
      value = { ...value, amount: Number(value.amount) };
    }
    fields[key] = {
      status: field.status,
      rule_ref: field.rule_ref,
      value,
      confidence: field.confidence ?? 0,
      bbox_height_mm: field.bbox_height_mm ?? undefined,
      reason: field.reason,
    };
  }

  return {
    id: `scan-${Date.now()}`,
    product_name: sourceLabel,
    manufacturer: 'Unknown (pending manual entry)',
    scan_date: new Date().toISOString(),
    scan_type: raw.is_ecommerce ? 'ecommerce' : 'physical',
    overall_status: raw.overall_status,
    is_ecommerce: raw.is_ecommerce,
    is_molded: raw.is_molded,
    usp_context: raw.usp_context,
    fields,
    violations: (raw.violations || []).map((v: any) => ({
      rule_ref: v.rule_ref,
      severity: v.severity,
      description: v.description,
    })),
    not_detected_fields: raw.not_detected_fields || [],
    preprocessing: raw.preprocessing,
    image_url: '',
    message: raw.message,
    __sourceFile: sourceFile,
  };
}
