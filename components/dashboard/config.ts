import {
  faChartLine,
  faHouseSignal,
  faMicrochip,
  faServer,
} from "@fortawesome/free-solid-svg-icons";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";

export type DashboardView = "overview" | "network" | "services" | "system";

export const NAV_ITEMS: ReadonlyArray<{
  id: DashboardView;
  label: string;
  icon: IconDefinition;
}> = [
  { id: "overview", label: "Overview", icon: faHouseSignal },
  { id: "network", label: "Network", icon: faChartLine },
  { id: "services", label: "Services", icon: faServer },
  { id: "system", label: "System", icon: faMicrochip },
];
