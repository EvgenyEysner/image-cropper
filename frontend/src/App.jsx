import { useMemo, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Container,
  FormControlLabel,
  FormLabel,
  MenuItem,
  Paper,
  Slider,
  Stack,
  Switch,
  TextField,
  Typography,
} from "@mui/material";

import UploadFileIcon from "@mui/icons-material/UploadFile";

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8010";

export default function App() {
  const [files, setFiles] = useState([]);
  const [format, setFormat] = useState("jpeg");
  const [quality, setQuality] = useState(90);
  const [bgColor, setBgColor] = useState("white");
  const [useHighQuality, setUseHighQuality] = useState(false);
  const [modelHint, setModelHint] = useState("high-quality");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [results, setResults] = useState([]);

  const hasFiles = useMemo(() => files.length > 0, [files]);

  const onFileChange = (event) => {
    const selected = Array.from(event.target.files || []);
    setFiles(selected);
    setResults([]);
    setError("");
  };

  const onSubmit = async (event) => {
    event.preventDefault();
    if (!hasFiles) {
      setError("Bitte mindestens ein Bild auswählen.");
      return;
    }

    try {
      setLoading(true);
      setError("");

      const formData = new FormData();
      files.forEach((file) => formData.append("files", file));
      formData.append("format", format);
      formData.append("quality", String(quality));
      formData.append("bg_color", bgColor);
      formData.append("model_hint", modelHint);

      const response = await fetch(`${API_BASE_URL}/cropping-image/upload-batch`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const text = await response.text();
        throw new Error(`API Fehler (${response.status}): ${text}`);
      }

      const payload = await response.json();
      setResults(payload.results || []);
    } catch (err) {
      setError(err.message || "Unerwarteter Fehler");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Container maxWidth="lg" sx={{ py: 4 }}>
      <Stack spacing={3}>
        <Box>
          <Typography variant="h4" fontWeight={700}>
            Image Cropper - Lokaltest
          </Typography>
          <Typography color="text.secondary">
            Mehrfach-Upload mit Vorher/Nachher Vorschau.
          </Typography>
        </Box>

        <Paper component="form" onSubmit={onSubmit} sx={{ p: 3 }}>
          <Stack spacing={2}>
            <Button component="label" variant="contained" startIcon={<UploadFileIcon />}>
              Bilder auswählen
              <input hidden multiple accept="image/*" type="file" onChange={onFileChange} />
            </Button>

            <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
              {files.map((file) => (
                <Chip key={file.name + file.size} label={file.name} />
              ))}
            </Stack>

            <Stack direction={{ xs: "column", sm: "row" }} spacing={2}>
              <TextField
                select
                label="Format"
                value={format}
                onChange={(e) => setFormat(e.target.value)}
                sx={{ minWidth: 140 }}
              >
                <MenuItem value="jpeg">JPEG</MenuItem>
                <MenuItem value="png">PNG</MenuItem>
              </TextField>

              <TextField
                label="BG Color"
                value={bgColor}
                onChange={(e) => setBgColor(e.target.value)}
                helperText='z.B. "white", "black", "#ffffff"'
                sx={{ minWidth: 220 }}
              />

              <TextField
                select
                label="Modell"
                value={modelHint}
                onChange={(e) => setModelHint(e.target.value)}
                sx={{ minWidth: 200 }}
              >
                <MenuItem value="product">Produkt (schnell, scharf)</MenuItem>
                <MenuItem value="high-quality">High Quality (langsamer)</MenuItem>
                <MenuItem value="person">Person / Körper</MenuItem>
                <MenuItem value="general">Allgemein (sehr schnell)</MenuItem>
              </TextField>
            </Stack>

            <Stack spacing={1}>
              <FormLabel>Qualität (nur für JPEG): {quality}</FormLabel>
              <Slider
                value={quality}
                min={1}
                max={100}
                step={1}
                marks={[
                  { value: 60, label: "60" },
                  { value: 80, label: "80" },
                  { value: 90, label: "90" },
                  { value: 100, label: "100" },
                ]}
                onChange={(_, value) => setQuality(Array.isArray(value) ? value[0] : value)}
                disabled={format !== "jpeg"}
              />
              <Stack direction="row" spacing={1}>
                <Button size="small" variant="outlined" onClick={() => setQuality(80)}>
                  Schnell (80)
                </Button>
                <Button size="small" variant="outlined" onClick={() => setQuality(90)}>
                  Standard (90)
                </Button>
                <Button size="small" variant="outlined" onClick={() => setQuality(98)}>
                  Sehr hoch (98)
                </Button>
              </Stack>
              <FormControlLabel
                control={
                  <Switch
                    checked={useHighQuality}
                    onChange={(e) => {
                      const checked = e.target.checked;
                      setUseHighQuality(checked);
                      setQuality(checked ? 98 : 90);
                    }}
                  />
                }
                label="High Quality Schnellschalter"
              />
            </Stack>

            <Box>
              <Button type="submit" variant="contained" disabled={loading || !hasFiles}>
                {loading ? "Verarbeite..." : "Freistellen starten"}
              </Button>
            </Box>
          </Stack>
        </Paper>

        {loading && (
          <Stack direction="row" spacing={1} alignItems="center">
            <CircularProgress size={20} />
            <Typography>Verarbeitung läuft...</Typography>
          </Stack>
        )}

        {error && <Alert severity="error">{error}</Alert>}

        <Box
          sx={{
            display: "grid",
            gridTemplateColumns: { xs: "1fr", md: "1fr 1fr" },
            gap: 2,
          }}
        >
          {results.map((item, index) => (
            <Card key={`${item.filename}-${index}`}>
              <CardContent>
                <Stack spacing={1}>
                  <Typography variant="h6">{item.filename || `Bild ${index + 1}`}</Typography>
                  <Chip
                    label={item.success ? "Erfolgreich" : "Fehlgeschlagen"}
                    color={item.success ? "success" : "error"}
                    size="small"
                    sx={{ width: "fit-content" }}
                  />
                  {item.message && <Typography color="text.secondary">{item.message}</Typography>}

                  {item.success && (
                    <Box
                      sx={{
                        display: "grid",
                        gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
                        gap: 2,
                      }}
                    >
                      <Box>
                        <Typography variant="subtitle2">Original</Typography>
                        <Box
                          component="img"
                          src={item.original_image_base64}
                          alt={`original-${item.filename}`}
                          sx={{ width: "100%", borderRadius: 1, border: "1px solid #ddd" }}
                        />
                      </Box>
                      <Box>
                        <Typography variant="subtitle2">Freigestellt</Typography>
                        <Box
                          component="img"
                          src={item.result_image_base64}
                          alt={`result-${item.filename}`}
                          sx={{ width: "100%", borderRadius: 1, border: "1px solid #ddd" }}
                        />
                      </Box>
                    </Box>
                  )}
                </Stack>
              </CardContent>
            </Card>
          ))}
        </Box>
      </Stack>
    </Container>
  );
}
