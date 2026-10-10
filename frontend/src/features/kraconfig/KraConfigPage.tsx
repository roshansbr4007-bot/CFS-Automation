/** Phase 7.5A: KRA configuration (HR prepares drafts, Admin activates and retires). The route is
 * open to configure_kpis or approve_kpi_config; the backend enforces every permission again. */
import { Box, Stack, Tab, Tabs, Typography } from "@mui/material";
import { useSearchParams } from "react-router-dom";

import { PERM } from "../../api/types";
import { useAuth } from "../../app/AuthProvider";
import { BandSchemesTab } from "./BandSchemesTab";
import { KpiMasterTab } from "./KpiMasterTab";
import { PlanSelectionTab } from "./PlanSelectionTab";
import { PlansTab } from "./PlansTab";
import { ScoringRulesTab } from "./ScoringRulesTab";

const TABS = [
  { key: "plans", label: "Plans" },
  { key: "scoring-rules", label: "Benchmark rules" },
  { key: "band-schemes", label: "Band schemes" },
  { key: "selection", label: "Plan selection" },
  { key: "kpis", label: "KPIs" },
] as const;
type TabKey = (typeof TABS)[number]["key"];

export function KraConfigPage() {
  const { hasPerm } = useAuth();
  const [params, setParams] = useSearchParams();
  const requested = params.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === requested) ? (requested as TabKey) : "plans";
  const canPrepare = hasPerm(PERM.configureKpis);
  return (
    <Stack spacing={3}>
      <Typography variant="h2" component="h1">KRA configuration</Typography>
      <Typography color="text.secondary">
        {canPrepare
          ? "Prepare draft plans, benchmark rules and band schemes. Admin activates a draft once it is complete; active and retired versions cannot be edited."
          : "Review what HR prepared, activate complete drafts and retire active versions. Active and retired versions cannot be edited."}
      </Typography>
      <Tabs value={tab} onChange={(_, value: TabKey) => setParams({ tab: value })} aria-label="KRA configuration sections">
        {TABS.map((t) => <Tab key={t.key} value={t.key} label={t.label} id={`kra-tab-${t.key}`} aria-controls={`kra-panel-${t.key}`} />)}
      </Tabs>
      <Box role="tabpanel" id={`kra-panel-${tab}`} aria-labelledby={`kra-tab-${tab}`}>
        {tab === "plans" && <PlansTab />}
        {tab === "scoring-rules" && <ScoringRulesTab />}
        {tab === "band-schemes" && <BandSchemesTab />}
        {tab === "selection" && <PlanSelectionTab />}
        {tab === "kpis" && <KpiMasterTab />}
      </Box>
    </Stack>
  );
}
