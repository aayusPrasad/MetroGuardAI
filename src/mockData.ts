export type OverallStatus = 'compliant' | 'non_compliant' | 'review_required' | 'insufficient_image_quality';
export type FieldStatus = 'compliant' | 'non_compliant' | 'not_detected';
export type Severity = 'critical' | 'major' | 'minor';
export type ScanType = 'physical' | 'ecommerce';

export type FieldValue = string | { amount: number; unit?: string; inclusive_of_all_taxes?: boolean } | string[];
export interface ScanField { status: FieldStatus; rule_ref: string; value?: FieldValue; confidence: number; bbox_height_mm?: number; reason?: string }
export interface Scan {
  id: string; product_name: string; manufacturer: string; scan_date: string; scan_type: ScanType; overall_status: OverallStatus;
  is_ecommerce: boolean; is_molded: boolean; usp_context: { required: boolean; reason: string };
  fields: Record<string, ScanField>; violations: { rule_ref: string; severity: Severity; description: string }[];
  not_detected_fields: string[]; preprocessing: { pixels_per_cm: number; pdp_area_cm2: number; calibration_method: 'manual_input' | 'marker_detected'; image_enhancement_applied: boolean; original_resolution: string; enhanced_resolution: string };
  image_url: string; message?: string;
}

export const FIELD_LABELS: Record<string, string> = {
  manufacturer_packer_importer: 'Manufacturer / Packer / Importer', generic_name: 'Generic / Common Name', net_quantity: 'Net Quantity', mrp: 'Maximum Retail Price (MRP)', manufacturing_date: 'Manufacturing Date', consumer_care: 'Consumer Care Details', unit_sale_price: 'Unit Sale Price (USP)', country_of_origin: 'Country of Origin'
};

const products = [
  ['Aashirvaad Whole Wheat Atta 10kg','ITC Limited'], ['Fortune Sunflower Oil 1L','Adani Wilmar Ltd.'], ['Maggi 2-Minute Noodles 70g','Nestlé India Ltd.'], ['Dettol Antiseptic Liquid 250ml','Reckitt Benckiser'], ['Tata Salt Iodised 1kg','Tata Consumer Products'], ['Amul Butter 500g','Gujarat Cooperative Milk Marketing Fed.'], ['Himalaya Neem Face Wash 150ml','The Himalaya Drug Company'], ['Surf Excel Matic 2kg','Hindustan Unilever'], ['Parle-G Glucose Biscuits 800g','Parle Products'], ['Dabur Honey 500g','Dabur India Ltd.'], ['Munch Nibbles Snack Pack 150g','Munch Nibbles Foods Pvt. Ltd.'], ['Britannia Good Day 250g','Britannia Industries'], ['Nescafé Classic 100g','Nestlé India Ltd.'], ['Kissan Mixed Fruit Jam 500g','Hindustan Unilever'], ['Patanjali Dant Kanti 100g','Patanjali Ayurved'], ['Colgate MaxFresh 150g','Colgate-Palmolive India'], ['Thums Up Carbonated Drink 750ml','Hindustan Coca-Cola'], ['Vim Dishwash Gel 500ml','Hindustan Unilever'], ['Mother Dairy Toned Milk 1L','Mother Dairy Fruit & Vegetable'], ['Glucon-D Tangy Orange 1kg','Hector Beverages'], ['Haldiram Bhujia 200g','Haldiram Snacks'], ['Pears Pure & Gentle 125g','Hindustan Unilever'], ['BoroPlus Antiseptic Cream 40ml','Emami Ltd.'], ['MDH Chana Masala 100g','Mahashian Di Hatti'], ['London Dairy Ice Cream 500ml','Havmor Ice Cream'], ['Tata Tea Gold 500g','Tata Consumer Products'], ['Paper Boat Aam Panna 600ml','Hector Beverages'], ['Nivea Soft Cream 100ml','Nivea India']
] as const;
const statuses: OverallStatus[] = ['review_required','compliant','non_compliant','review_required','compliant','non_compliant','compliant','non_compliant','compliant','compliant','review_required','compliant','non_compliant','compliant','compliant','non_compliant','compliant','insufficient_image_quality','compliant','review_required','non_compliant','compliant','compliant','non_compliant','compliant','review_required','compliant','compliant'];
const fieldKeys = ['manufacturer_packer_importer','generic_name','net_quantity','mrp','manufacturing_date','consumer_care','unit_sale_price'];
const dateFor = (index: number) => new Date(Date.UTC(2026, 8, 6 - Math.floor(index * 2.2), 9 - (index % 5), 15 + index)).toISOString();
const makeFields = (index: number, status: OverallStatus): Record<string, ScanField> => {
  if (status === 'insufficient_image_quality') return {};
  const missing = status === 'review_required' ? fieldKeys[index % fieldKeys.length] : index % 9 === 0 ? 'country_of_origin' : '';
  return Object.fromEntries(fieldKeys.concat(index % 4 === 0 ? ['country_of_origin'] : []).map((key, fieldIndex) => {
    const notDetected = key === missing;
    const value: FieldValue = key === 'net_quantity' ? { amount: 500 + index * 10, unit: 'g' } : key === 'mrp' ? { amount: 85 + index * 5, inclusive_of_all_taxes: true } : key === 'consumer_care' ? ['1800-123-4567', 'care@metroguard.demo'] : key === 'unit_sale_price' ? { amount: 0.17 + index / 100, unit: 'per 10g' } : key === 'manufacturing_date' ? '08/2026' : key === 'generic_name' ? 'Packaged consumer product' : key === 'country_of_origin' ? 'India' : products[index][1];
    return [key, { status: notDetected ? 'not_detected' : status === 'non_compliant' && fieldIndex === index % 4 ? 'non_compliant' : 'compliant', rule_ref: `LM-${String(101 + fieldIndex).padStart(3, '0')}`, value, confidence: Math.max(0.55, 0.98 - (index % 7) * 0.035), bbox_height_mm: key === 'mrp' ? 2.4 : 3.1, ...(notDetected ? { reason: 'Text region is obscured or below the minimum readable threshold.' } : {}) }];
  }));
};

export const scans: Scan[] = products.map(([product_name, manufacturer], index) => {
  const overall_status = statuses[index];
  const is_ecommerce = index === 6 || index === 17 || index === 25;
  const violations: { rule_ref: string; severity: Severity; description: string }[] = overall_status === 'non_compliant' ? [
    { rule_ref: 'LM-104', severity: index % 3 === 0 ? 'critical' : 'major', description: 'Mandatory declaration is not displayed in the prescribed format.' },
    ...(index % 2 === 0 ? [{ rule_ref: 'LM-108', severity: 'minor' as Severity, description: 'Unit sale price is missing or not legible.' }] : [])
  ] : overall_status === 'review_required' ? [{ rule_ref: 'LM-103', severity: 'major' as Severity, description: 'A mandatory field needs manual confirmation.' }] : [];
  return { id: `scan-2026-${String(892 - index).padStart(4, '0')}`, product_name, manufacturer, scan_date: dateFor(index), scan_type: is_ecommerce ? 'ecommerce' : 'physical', overall_status, is_ecommerce, is_molded: index % 4 === 0, usp_context: { required: !is_ecommerce, reason: is_ecommerce ? 'Unit sale price and font-size checks do not apply to e-commerce product pages.' : `USP calculated from MRP ₹${85 + index * 5} and net quantity ${500 + index * 10}g: ₹${(0.17 + index / 100).toFixed(2)} per 10g.` }, fields: makeFields(index, overall_status), violations, not_detected_fields: Object.entries(makeFields(index, overall_status)).filter(([, field]) => field.status === 'not_detected').map(([key]) => key), preprocessing: { pixels_per_cm: 118 + index, pdp_area_cm2: 420 + index * 8, calibration_method: index % 3 === 0 ? 'manual_input' : 'marker_detected', image_enhancement_applied: index % 2 === 0, original_resolution: '3024 × 4032 px', enhanced_resolution: '6048 × 8064 px' }, image_url: '', ...(overall_status === 'insufficient_image_quality' ? { message: 'The captured image resolution is 640 × 480 px, below the required minimum of 1280 × 720 px. Please recapture the product image with better lighting and focus.' } : {}) };
});

const wait = (ms = 520) => new Promise(resolve => setTimeout(resolve, ms));
export interface ScanFilters { search?: string; status?: OverallStatus | 'all'; type?: ScanType | 'all' }
export async function listScans(filters: ScanFilters = {}) { await wait(); const query = filters.search?.toLowerCase().trim() ?? ''; return scans.filter(scan => (!query || `${scan.id} ${scan.product_name} ${scan.manufacturer}`.toLowerCase().includes(query)) && (!filters.status || filters.status === 'all' || scan.overall_status === filters.status) && (!filters.type || filters.type === 'all' || scan.scan_type === filters.type)); }
export async function getScanById(id: string) { await wait(360); return scans.find(scan => scan.id === id) ?? null; }
export async function submitScan(payload: { productName?: string; ecommerce?: boolean; qualityIssue?: boolean }) { await wait(900); const template = scans[10]; const next: Scan = { ...template, id: `scan-2026-${Date.now().toString().slice(-4)}`, product_name: payload.productName || (payload.ecommerce ? 'New E-Commerce Product Page' : 'New Uploaded Product'), scan_date: new Date().toISOString(), is_ecommerce: Boolean(payload.ecommerce), scan_type: payload.ecommerce ? 'ecommerce' : 'physical', overall_status: payload.qualityIssue ? 'insufficient_image_quality' : 'compliant', fields: payload.qualityIssue ? {} : template.fields, not_detected_fields: payload.qualityIssue ? [] : template.not_detected_fields, message: payload.qualityIssue ? 'The captured image is below the minimum required resolution. Please recapture with better lighting and focus.' : undefined }; scans.unshift(next); return next; }
export async function getReviewItems() { await wait(); return scans.flatMap(scan => scan.not_detected_fields.map(field => ({ scan, field }))).sort((a, b) => +new Date(b.scan.scan_date) - +new Date(a.scan.scan_date)); }
export function resolveReview(scanId: string, fieldId: string, compliant: boolean) { const scan = scans.find(item => item.id === scanId); if (!scan) return; const field = scan.fields[fieldId]; if (field) { field.status = compliant ? 'compliant' : 'non_compliant'; field.reason = undefined; } scan.not_detected_fields = scan.not_detected_fields.filter(fieldName => fieldName !== fieldId); if (!compliant) { scan.overall_status = 'non_compliant'; scan.violations.push({ rule_ref: field?.rule_ref || 'LM-000', severity: 'major', description: `${FIELD_LABELS[fieldId]} requires enforcement action.` }); } else if (!scan.not_detected_fields.length && scan.overall_status === 'review_required') scan.overall_status = 'compliant'; }
