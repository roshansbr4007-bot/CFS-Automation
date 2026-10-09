import NotificationsIcon from "@mui/icons-material/Notifications";
import { Badge, Box, Button, IconButton, Menu, MenuItem, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { notificationsApi } from "../../api/endpoints";
import { DateTimeText } from "../../components/DateTimeText";

/** In-app SLA notifications. The backend decides who is notified and when. */
export function NotificationBell() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const { data: count } = useQuery({ queryKey: ["notifications", "count"], queryFn: notificationsApi.unreadCount, refetchInterval: 60_000 });
  const { data: list } = useQuery({ queryKey: ["notifications", "list"], queryFn: () => notificationsApi.list({ page: 1 }), enabled: !!anchor });
  const refresh = () => qc.invalidateQueries({ queryKey: ["notifications"] });
  const read = useMutation({ mutationFn: notificationsApi.markRead, onSuccess: refresh });
  const readAll = useMutation({ mutationFn: notificationsApi.markAllRead, onSuccess: refresh });
  const unread = count?.unread ?? 0;

  return (
    <>
      <IconButton aria-label={`Notifications, ${unread} unread`} onClick={(e) => setAnchor(e.currentTarget)}>
        <Badge badgeContent={unread} color="error"><NotificationsIcon /></Badge>
      </IconButton>
      <Menu anchorEl={anchor} open={!!anchor} onClose={() => setAnchor(null)} slotProps={{ paper: { sx: { width: 380, maxHeight: 440 } } }}>
        <Box sx={{ px: 2, py: 1, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <Typography variant="subtitle2">Notifications</Typography>
          <Button size="small" disabled={unread === 0} onClick={() => readAll.mutate()}>Mark all read</Button>
        </Box>
        {(list?.results ?? []).length === 0 && <MenuItem disabled>No notifications.</MenuItem>}
        {(list?.results ?? []).map((n) => (
          <MenuItem
            key={n.id}
            onClick={() => {
              if (!n.read_at) read.mutate(n.id);
              setAnchor(null);
              if (n.task) navigate(`/tasks/${n.task}`);
            }}
            sx={{ whiteSpace: "normal", alignItems: "flex-start", fontWeight: n.read_at ? 400 : 600 }}
          >
            <Box>
              <Typography variant="body2" sx={{ fontWeight: "inherit" }}>{n.title}</Typography>
              <Typography variant="caption" color="text.secondary"><DateTimeText value={n.created_at} /></Typography>
            </Box>
          </MenuItem>
        ))}
      </Menu>
    </>
  );
}
