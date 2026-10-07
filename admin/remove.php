<?php
// (c) Dr. Ralf Korell
// Galerist — Admin: Loeschmarker setzen (Bild aus der Galerie entfernen)
// Modified: 2026-10-06 - Erstellt
// Modified: 2026-10-07 - Setzt to_be_deleted statt verworfen/verworfen_am; ein UPDATE, kein RETURNING von Metadaten
//
// Setzt ausschliesslich das Flag bilder.to_be_deleted. Geloescht wird nichts —
// die Dateien raeumt der naechtliche Cronjob (galerie_aufraeumen.sh) ab und
// setzt danach verworfen = true, to_be_deleted = false.
// Aufrufer ist die Steuer-App (PWA) auf den Rahmen, LAN-only, ohne Login
// (bewusst, konsistent zum uebrigen Galerist-Design).

declare(strict_types=1);

require_once __DIR__ . '/db_connect.php';

// ── CORS: nur die Rahmen-Herkunftsadressen ────────────────────────────────
$origin = $_SERVER['HTTP_ORIGIN'] ?? '';
if (in_array($origin, GAL_ALLOWED_ORIGINS, true)) {
    header('Access-Control-Allow-Origin: ' . $origin);
    header('Access-Control-Allow-Headers: Content-Type');
    header('Access-Control-Allow-Methods: POST, OPTIONS');
}

if (($_SERVER['REQUEST_METHOD'] ?? '') === 'OPTIONS') {
    http_response_code(204);
    exit;
}

header('Content-Type: application/json; charset=utf-8');

function antwort(int $code, array $daten): never
{
    http_response_code($code);
    echo json_encode($daten, JSON_UNESCAPED_UNICODE);
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    antwort(405, ['status' => 'fehler', 'meldung' => 'nur POST']);
}

// ── Eingabe ──────────────────────────────────────────────────────────────
$eingabe = json_decode(file_get_contents('php://input') ?: '', true);
$dateiname = is_array($eingabe) ? trim((string)($eingabe['dateiname'] ?? '')) : '';

// Dateiname ist der Schluessel (UNIQUE in bilder) und spaeter ein Pfadbestandteil
// im Cronjob — daher hier eng pruefen: keine Pfadtrenner, keine Sonderzeichen.
if ($dateiname === '' || !preg_match('/^[A-Za-z0-9._-]+\.jpg$/', $dateiname)) {
    antwort(400, ['status' => 'fehler', 'meldung' => 'ungueltiger Dateiname']);
}

// ── Flag setzen ──────────────────────────────────────────────────────────
try {
    $db = new PDO(GAL_DB_DSN, GAL_DB_USER, GAL_DB_PASS, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);

    $stmt = $db->prepare(
        'UPDATE bilder SET to_be_deleted = true
          WHERE dateiname = :d AND NOT to_be_deleted'
    );
    $stmt->execute([':d' => $dateiname]);

    if ($stmt->rowCount() === 1) {
        antwort(200, ['status' => 'markiert', 'dateiname' => $dateiname]);
    }

    // Kein Treffer: entweder schon markiert (idempotent, kein Fehler) oder unbekannt.
    $pruef = $db->prepare('SELECT 1 FROM bilder WHERE dateiname = :d');
    $pruef->execute([':d' => $dateiname]);

    if ($pruef->fetch()) {
        antwort(200, ['status' => 'schon_markiert', 'dateiname' => $dateiname]);
    }

    antwort(404, ['status' => 'fehler', 'meldung' => 'Bild nicht in der Datenbank',
                  'dateiname' => $dateiname]);

} catch (PDOException $e) {
    error_log('galerist/remove.php: ' . $e->getMessage());
    antwort(500, ['status' => 'fehler', 'meldung' => 'Datenbankfehler']);
}
