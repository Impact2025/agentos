// Opslag voor LSP-workshop-inzendingen (schema.sql: lsp_submissions). Eigen
// bestand omdat zowel het WhatsApp-pad (whatsapp.js) als een e-mailpad
// dezelfde rij-vorm wegschrijven — één plek die de kolommen kent.
import { sql } from './_lib.js';

export async function insertSubmission({
  tenant, source, sender, contactName, teamLabel, noteText, imageDataUrl,
  dashboardSummary, participantReport, error,
}) {
  const rows = await sql`
    INSERT INTO lsp_submissions (
      tenant, source, sender, contact_name, team_label, note_text, image_data_url,
      dashboard_summary, participant_report, status, error, processed_at
    ) VALUES (
      ${tenant}, ${source}, ${sender}, ${contactName || null}, ${teamLabel || null},
      ${noteText || null}, ${imageDataUrl || null}, ${dashboardSummary || null},
      ${participantReport || null}, ${error ? 'fout' : 'verwerkt'}, ${error || null}, now()
    )
    RETURNING id`;
  return rows[0]?.id || null;
}
