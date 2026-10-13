"use client";

import * as React from "react";
import { Zap, FileText, Landmark, BrainCircuit, Clock } from "lucide-react";
import { Header } from "@/components/dashboard/header";
import { BankrollCards } from "@/components/dashboard/bankroll-cards";
import { MatchesTab } from "@/components/dashboard/tabs/matches-tab";
import { BetsTab } from "@/components/dashboard/tabs/bets-tab";
import { LedgerTab } from "@/components/dashboard/tabs/ledger-tab";
import { ModelsTab } from "@/components/dashboard/tabs/models-tab";
import { QueueTab } from "@/components/dashboard/tabs/queue-tab";
import { EventDeepdiveModal } from "@/components/dashboard/modal/event-deepdive-modal";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  fetchAccount,
  fetchPerformance,
  fetchSports,
  fetchEvents,
  fetchPredictions,
  fetchBets,
  fetchLedger,
  fetchModels,
  fetchTrainingRuns,
  fetchSchedulerQueue,
  fetchSchedulerStats,
  fetchEventDetails,
  resetSimulationApi,
} from "@/lib/api";
import {
  BankrollData,
  PerformanceData,
  SportItem,
  EventItem,
  PredictionItem,
  BetItem,
  LedgerEntry,
  ModelItem,
  TrainingRun,
  QueueItem,
  QueueStats,
  EventDetailData,
} from "@/lib/types";
import { getSportNameRu } from "@/lib/formatters";

export default function DashboardPage() {
  const [activeTab, setActiveTab] = React.useState<string>("matches");
  const [activeSport, setActiveSport] = React.useState<string>("tennis");
  const [activeStatus, setActiveStatus] = React.useState<string>("live");
  const [searchQuery, setSearchQuery] = React.useState<string>("");

  const [bankroll, setBankroll] = React.useState<BankrollData | null>(null);
  const [performance, setPerformance] = React.useState<PerformanceData | null>(null);
  const [sports, setSports] = React.useState<SportItem[]>([]);
  const [events, setEvents] = React.useState<EventItem[]>([]);
  const [predictions, setPredictions] = React.useState<Record<string, PredictionItem[]>>({});
  const [bets, setBets] = React.useState<BetItem[]>([]);
  const [ledger, setLedger] = React.useState<LedgerEntry[]>([]);
  const [models, setModels] = React.useState<ModelItem[]>([]);
  const [trainingRuns, setTrainingRuns] = React.useState<TrainingRun[]>([]);
  const [queue, setQueue] = React.useState<QueueItem[]>([]);
  const [queueStats, setQueueStats] = React.useState<QueueStats | null>(null);

  const [selectedEventData, setSelectedEventData] = React.useState<EventDetailData | null>(null);
  const [modalOpen, setModalOpen] = React.useState<boolean>(false);
  const [sseConnected, setSseConnected] = React.useState<boolean>(false);
  const [isResetting, setIsResetting] = React.useState<boolean>(false);
  const [loading, setLoading] = React.useState<boolean>(true);

  // Load initial bankroll & sports
  const loadInitialData = React.useCallback(async () => {
    try {
      const [acc, perf, spList] = await Promise.all([
        fetchAccount(),
        fetchPerformance(),
        fetchSports(),
      ]);
      if (acc) setBankroll(acc);
      if (perf) setPerformance(perf);
      if (spList && spList.length > 0) {
        setSports(spList);
        if (!activeSport) setActiveSport(spList[0].code);
      }
    } catch (e) {
      console.error("[Dashboard] loadInitialData error:", e);
    }
  }, [activeSport]);

  // Load tab-specific data
  const loadTabContent = React.useCallback(async () => {
    setLoading(true);
    try {
      if (activeTab === "matches") {
        const [evs, preds] = await Promise.all([
          fetchEvents(activeSport, activeStatus, searchQuery),
          fetchPredictions(),
        ]);
        if (evs) setEvents(evs);
        if (preds && preds.length > 0) {
          const pMap: Record<string, PredictionItem[]> = {};
          preds.forEach((p) => {
            if (!pMap[p.event_id]) pMap[p.event_id] = [];
            pMap[p.event_id].push(p);
          });
          setPredictions(pMap);
        }
      } else if (activeTab === "bets") {
        const bList = await fetchBets(50);
        setBets(bList);
      } else if (activeTab === "ledger") {
        const lList = await fetchLedger(50);
        setLedger(lList);
      } else if (activeTab === "models") {
        const [mList, tList] = await Promise.all([fetchModels(), fetchTrainingRuns()]);
        setModels(mList);
        setTrainingRuns(tList);
      } else if (activeTab === "scheduler") {
        const [qList, qSt] = await Promise.all([
          fetchSchedulerQueue(),
          fetchSchedulerStats(),
        ]);
        setQueue(qList);
        if (qSt) setQueueStats(qSt);
      }
    } catch (err) {
      console.error("[Dashboard] loadTabContent error:", err);
    } finally {
      setLoading(false);
    }
  }, [activeTab, activeSport, activeStatus, searchQuery]);

  React.useEffect(() => {
    loadInitialData();
  }, [loadInitialData]);

  React.useEffect(() => {
    loadTabContent();
  }, [loadTabContent]);

  const viewRef = React.useRef({
    activeTab,
    activeSport,
    activeStatus,
    searchQuery,
    modalOpen,
    eventId: null as string | null,
  });
  viewRef.current = {
    activeTab,
    activeSport,
    activeStatus,
    searchQuery,
    modalOpen,
    eventId: selectedEventData?.evt?.id ?? null,
  };

  const applyPredictions = React.useCallback((preds: PredictionItem[]) => {
    const pMap: Record<string, PredictionItem[]> = {};
    preds.forEach((p) => {
      if (!pMap[p.event_id]) pMap[p.event_id] = [];
      pMap[p.event_id].push(p);
    });
    setPredictions(pMap);
  }, []);

  // Тихое обновление счёта, коэффициентов и банкролла без перезагрузки страницы.
  const refreshLive = React.useCallback(async () => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
    const view = viewRef.current;
    try {
      const [acc, perf] = await Promise.all([fetchAccount(), fetchPerformance()]);
      if (acc) setBankroll(acc);
      if (perf) setPerformance(perf);

      if (view.activeTab === "matches") {
        const [evs, preds] = await Promise.all([
          fetchEvents(view.activeSport, view.activeStatus, view.searchQuery),
          fetchPredictions(),
        ]);
        // Keep previous board on empty/failed polls so the live list does not flicker away.
        if (evs && evs.length > 0) setEvents(evs);
        if (preds && preds.length > 0) applyPredictions(preds);
        if (view.modalOpen && view.eventId) {
          const details = await fetchEventDetails(view.eventId);
          if (details) setSelectedEventData(details);
        }
      } else if (view.activeTab === "bets") {
        const bList = await fetchBets(50);
        if (bList.length > 0) setBets(bList);
      } else if (view.activeTab === "ledger") {
        const lList = await fetchLedger(50);
        if (lList.length > 0) setLedger(lList);
      } else if (view.activeTab === "scheduler") {
        const [qList, qSt] = await Promise.all([fetchSchedulerQueue(), fetchSchedulerStats()]);
        if (qList.length > 0) setQueue(qList);
        if (qSt) setQueueStats(qSt);
      }
    } catch (err) {
      console.error("[Dashboard] refreshLive error:", err);
    }
  }, [applyPredictions]);

  React.useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshLive();
    }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshLive]);

  // SSE Stream listener. EventSource retries on its own; a short blip
  // must not flip the header into "Подключение..." and open a second socket.
  React.useEffect(() => {
    let sse: EventSource | null = null;
    let offlineTimer: NodeJS.Timeout | null = null;
    let stopped = false;

    const isSubpath =
      typeof window !== "undefined" &&
      window.location.pathname.includes("/diflector/neurobet");
    const streamUrl = isSubpath
      ? "/diflector/neurobet/api/live/stream"
      : "/api/live/stream";

    const clearOfflineTimer = () => {
      if (offlineTimer) {
        clearTimeout(offlineTimer);
        offlineTimer = null;
      }
    };

    const markOpen = () => {
      clearOfflineTimer();
      setSseConnected(true);
    };

    sse = new EventSource(streamUrl);

    sse.onopen = () => {
      markOpen();
    };

    sse.addEventListener("heartbeat", () => {
      markOpen();
    });

    sse.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.type === "BANKROLL_UPDATE" && data.bankroll) {
          setBankroll(data.bankroll);
        } else if (data.type === "EVENT_UPDATE") {
          void refreshLive();
        }
      } catch {
        // ignore non-json messages (keep-alive)
      }
    };

    sse.onerror = () => {
      if (stopped) return;
      clearOfflineTimer();
      offlineTimer = setTimeout(() => {
        if (!stopped && sse && sse.readyState !== EventSource.OPEN) {
          setSseConnected(false);
        }
      }, 8000);
    };

    return () => {
      stopped = true;
      clearOfflineTimer();
      if (sse) sse.close();
    };
  }, [refreshLive]);

  // Inspect event deep-dive modal
  const handleInspectEvent = async (eventId: string) => {
    const details = await fetchEventDetails(eventId);
    if (details) {
      setSelectedEventData(details);
      setModalOpen(true);
    }
  };

  // Reset simulation handler
  const handleResetSimulation = async () => {
    setIsResetting(true);
    try {
      const ok = await resetSimulationApi();
      if (ok) {
        await loadInitialData();
        await loadTabContent();
      }
    } finally {
      setIsResetting(false);
    }
  };

  return (
    <div className="min-h-screen flex flex-col bg-background text-foreground">
      {/* Top Header */}
      <Header
        sseConnected={sseConnected}
        activeSportName={getSportNameRu(activeSport)}
        onResetSimulation={handleResetSimulation}
        isResetting={isResetting}
      />

      {/* Main Container */}
      <main className="flex-1 container mx-auto px-4 sm:px-6 py-5 space-y-5">
        {/* Bankroll & Performance Cards Banner */}
        <section aria-label="Сводка виртуального банкролла">
          <BankrollCards bankroll={bankroll} performance={performance} />
        </section>

        {/* Navigation Tabs */}
        <Tabs
          value={activeTab}
          onValueChange={setActiveTab}
          className="w-full space-y-4"
        >
          <TabsList className="w-full grid grid-cols-2 md:grid-cols-5 gap-1.5 p-1.5 bg-muted/30 border border-border/60 rounded-xl shadow-xs">
            <TabsTrigger
              value="matches"
              className="w-full py-2.5 px-3 gap-2 data-active:bg-card data-active:text-emerald-500 dark:data-active:text-emerald-400"
            >
              <Zap className="h-4 w-4 text-emerald-500 shrink-0" />
              <span className="truncate">Live Матчи</span>
            </TabsTrigger>

            <TabsTrigger
              value="bets"
              className="w-full py-2.5 px-3 gap-2 data-active:bg-card data-active:text-blue-500 dark:data-active:text-blue-400"
            >
              <FileText className="h-4 w-4 text-blue-500 shrink-0" />
              <span className="truncate">Журнал Ставок</span>
            </TabsTrigger>

            <TabsTrigger
              value="ledger"
              className="w-full py-2.5 px-3 gap-2 data-active:bg-card data-active:text-amber-500 dark:data-active:text-amber-400"
            >
              <Landmark className="h-4 w-4 text-amber-500 shrink-0" />
              <span className="truncate">Аудит журнала</span>
            </TabsTrigger>

            <TabsTrigger
              value="models"
              className="w-full py-2.5 px-3 gap-2 data-active:bg-card data-active:text-indigo-500 dark:data-active:text-indigo-400"
            >
              <BrainCircuit className="h-4 w-4 text-indigo-500 shrink-0" />
              <span className="truncate">Реестр Моделей ML</span>
            </TabsTrigger>

            <TabsTrigger
              value="scheduler"
              className="w-full py-2.5 px-3 gap-2 col-span-2 md:col-span-1 data-active:bg-card data-active:text-purple-500 dark:data-active:text-purple-400"
            >
              <Clock className="h-4 w-4 text-purple-500 shrink-0" />
              <span className="truncate">Очередь Планировщика</span>
            </TabsTrigger>
          </TabsList>

          {/* Tab 1: Matches & Value Bets */}
          <TabsContent value="matches" className="mt-0 focus-visible:outline-none">
            <MatchesTab
              events={events}
              predictions={predictions}
              sports={sports}
              activeSport={activeSport}
              onSelectSport={setActiveSport}
              activeStatus={activeStatus}
              onSelectStatus={setActiveStatus}
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              onInspectEvent={handleInspectEvent}
              loading={loading}
            />
          </TabsContent>

          {/* Tab 2: Virtual Bets */}
          <TabsContent value="bets" className="mt-0 focus-visible:outline-none">
            <BetsTab bets={bets} loading={loading} />
          </TabsContent>

          {/* Tab 3: Ledger Audit */}
          <TabsContent value="ledger" className="mt-0 focus-visible:outline-none">
            <LedgerTab ledger={ledger} loading={loading} />
          </TabsContent>

          {/* Tab 4: ML Models */}
          <TabsContent value="models" className="mt-0 focus-visible:outline-none">
            <ModelsTab models={models} trainingRuns={trainingRuns} loading={loading} />
          </TabsContent>

          {/* Tab 5: Scheduler Priority Queue */}
          <TabsContent value="scheduler" className="mt-0 focus-visible:outline-none">
            <QueueTab queue={queue} stats={queueStats} loading={loading} />
          </TabsContent>
        </Tabs>
      </main>

      {/* Deep-Dive Event Modal */}
      <EventDeepdiveModal
        data={selectedEventData}
        open={modalOpen}
        onOpenChange={setModalOpen}
      />

      {/* Footer */}
      <footer className="border-t border-border/40 py-4 bg-muted/20 text-center text-xs text-muted-foreground mt-8">
        <div className="container mx-auto px-4">
          Neurobet • Платформа спортивной аналитики и прогнозирования
        </div>
      </footer>
    </div>
  );
}
