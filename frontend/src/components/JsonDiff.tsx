import { Box, Typography } from "@mui/material";

import { tokens } from "../app/theme";

function Block({ label, value }: { label: string; value: unknown }) {
  return (
    <Box sx={{ minWidth: 0 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 0.5 }}>
        {label}
      </Typography>
      <Box
        component="pre"
        sx={{
          m: 0,
          p: 1.5,
          bgcolor: tokens.paper,
          border: `1px solid ${tokens.line}`,
          borderRadius: 1,
          fontSize: "0.8125rem",
          overflowX: "auto",
          whiteSpace: "pre-wrap",
        }}
      >
        {value === null || value === undefined ? "—" : JSON.stringify(value, null, 2)}
      </Box>
    </Box>
  );
}

export function JsonDiff({ before, after }: { before: unknown; after: unknown }) {
  return (
    <Box sx={{ display: "grid", gap: 2, gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" } }}>
      <Block label="Before" value={before} />
      <Block label="After" value={after} />
    </Box>
  );
}
