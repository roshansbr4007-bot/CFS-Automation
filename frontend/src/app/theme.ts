import { createTheme } from "@mui/material/styles";

// Palette: ledger ink for structure, a single sandstone-rose accent (Jaipur) used sparingly.
export const tokens = {
  ink: "#22385C",
  inkDeep: "#172742",
  paper: "#F5F6F8",
  surface: "#FFFFFF",
  text: "#1B2230",
  muted: "#5A6478",
  line: "#DDE1E8",
  rose: "#A8455E",
};

export const theme = createTheme({
  palette: {
    primary: { main: tokens.ink, dark: tokens.inkDeep, contrastText: "#FFFFFF" },
    secondary: { main: tokens.rose },
    background: { default: tokens.paper, paper: tokens.surface },
    text: { primary: tokens.text, secondary: tokens.muted },
    divider: tokens.line,
  },
  shape: { borderRadius: 6 },
  typography: {
    fontFamily: '"IBM Plex Sans", "Segoe UI", system-ui, sans-serif',
    h1: { fontSize: "2rem", fontWeight: 600, lineHeight: 1.2 },
    h2: { fontSize: "1.5rem", fontWeight: 600, lineHeight: 1.25 },
    h3: { fontSize: "1.25rem", fontWeight: 600, lineHeight: 1.3 },
    body1: { fontSize: "1rem", lineHeight: 1.55 },
    body2: { fontSize: "0.875rem", lineHeight: 1.5 },
    button: { textTransform: "none", fontWeight: 500 },
  },
  components: {
    MuiButton: { defaultProps: { disableElevation: true } },
    MuiAppBar: { defaultProps: { elevation: 0, color: "inherit" } },
    MuiPaper: { defaultProps: { variant: "outlined" } },
    MuiTableCell: {
      styleOverrides: {
        head: { fontWeight: 600, color: tokens.muted, fontSize: "0.8125rem" },
      },
    },
    MuiTextField: { defaultProps: { size: "small", fullWidth: true } },
  },
});
