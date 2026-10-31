import { Ionicons } from "@expo/vector-icons";
import { NavigationContainer, RouteProp, useFocusEffect, useNavigation, useRoute } from "@react-navigation/native";
import { createNativeStackNavigator, NativeStackNavigationProp } from "@react-navigation/native-stack";
import * as DocumentPicker from "expo-document-picker";
import { StatusBar } from "expo-status-bar";
import React, { useCallback, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Linking,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";

import { JobBlastApi } from "./src/api";
import type { ApiSettings, DocumentItem, HomePayload, Job, Source } from "./src/types";

type RootStackParamList = {
  Home: undefined;
  Jobs: { listName: "top" | "new" | "pipeline" | "public" | "letters"; title: string };
  JobDetail: { jobId: string };
  Generated: { jobId: string; kind: "cover-letter" | "answers" | "gap-analysis"; title: string };
  Settings: undefined;
  Documents: undefined;
  Sources: undefined;
  Connection: undefined;
};

const Stack = createNativeStackNavigator<RootStackParamList>();

const DEFAULT_SETTINGS: ApiSettings = {
  baseUrl: "http://10.0.2.2:8000",
  token: "change-this-local-development-token",
};

function useApi(settings: ApiSettings) {
  return useMemo(() => new JobBlastApi(settings), [settings.baseUrl, settings.token]);
}

function Shell({ children }: { children: React.ReactNode }) {
  return <SafeAreaView style={styles.shell}>{children}</SafeAreaView>;
}

function SectionTitle({ title, action }: { title: string; action?: React.ReactNode }) {
  return (
    <View style={styles.sectionHeader}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {action}
    </View>
  );
}

function IconButton({
  icon,
  label,
  onPress,
  variant = "primary",
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  onPress: () => void;
  variant?: "primary" | "secondary" | "danger";
}) {
  return (
    <Pressable style={[styles.button, styles[`${variant}Button`]]} onPress={onPress}>
      <Ionicons name={icon} size={18} color={variant === "secondary" ? "#0f172a" : "#ffffff"} />
      <Text style={[styles.buttonText, variant === "secondary" && styles.secondaryButtonText]}>{label}</Text>
    </Pressable>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function StatusLine({ icon, text }: { icon: keyof typeof Ionicons.glyphMap; text: string }) {
  return (
    <View style={styles.statusLine}>
      <Ionicons name={icon} size={18} color="#2563eb" />
      <Text style={styles.statusText}>{text}</Text>
    </View>
  );
}

function JobRow({ job, onPress }: { job: Job; onPress: () => void }) {
  return (
    <Pressable style={styles.jobRow} onPress={onPress}>
      <View style={styles.jobScore}>
        <Text style={styles.scoreValue}>{job.fit_score ?? 0}</Text>
        <Text style={styles.scoreLabel}>score</Text>
      </View>
      <View style={styles.jobBody}>
        <Text style={styles.jobTitle}>{job.title || "Untitled role"}</Text>
        <Text style={styles.jobMeta}>
          {job.company || "Unknown"} | {job.location || "Not stated"}
        </Text>
        <Text style={styles.jobState}>{job.state || "matched"}</Text>
      </View>
      <Ionicons name="chevron-forward" size={20} color="#64748b" />
    </Pressable>
  );
}

function HomeScreen({ api }: { api: JobBlastApi }) {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [home, setHome] = useState<HomePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [scanLoading, setScanLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setHome(await api.home());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load JobBlast.");
    } finally {
      setLoading(false);
    }
  }, [api]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const startScan = async () => {
    setScanLoading(true);
    try {
      const result = await api.scan();
      Alert.alert("Scan complete", `New jobs: ${result.new_jobs}\nPublic matches: ${result.public_matches}\nErrors: ${result.errors.length}`);
      await load();
    } catch (err) {
      Alert.alert("Scan failed", err instanceof Error ? err.message : "Could not run scan.");
    } finally {
      setScanLoading(false);
    }
  };

  return (
    <Shell>
      <ScrollView refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />} contentContainerStyle={styles.content}>
        <View style={styles.hero}>
          <View style={styles.brandMark}>
            <Ionicons name="briefcase" size={24} color="#ffffff" />
          </View>
          <Text style={styles.heroTitle}>JobBlast</Text>
          <Text style={styles.heroText}>Review matched jobs, prepare tailored documents, and track manual applications from your phone.</Text>
          <View style={styles.heroActions}>
            <IconButton icon="search" label={scanLoading ? "Scanning" : "Scan Jobs"} onPress={startScan} />
            <IconButton icon="settings-outline" label="Settings" variant="secondary" onPress={() => navigation.navigate("Settings")} />
          </View>
        </View>

        {error ? (
          <Pressable style={styles.errorBox} onPress={() => navigation.navigate("Connection")}>
            <Text style={styles.errorTitle}>Connection issue</Text>
            <Text style={styles.errorText}>{error}</Text>
          </Pressable>
        ) : null}

        {!home && loading ? <ActivityIndicator color="#2563eb" /> : null}
        {home ? (
          <>
            <View style={styles.metricsGrid}>
              <Metric label="Matched" value={home.counts.matched || 0} />
              <Metric label="Saved" value={home.counts.saved || 0} />
              <Metric label="Ready" value={home.counts.ready_for_manual_apply || 0} />
              <Metric label="Applied" value={home.counts.applied_by_user || 0} />
            </View>

            <SectionTitle title="Workspace" />
            <View style={styles.panel}>
              <StatusLine icon="document-text-outline" text={`Resume: ${home.resume_status}`} />
              <StatusLine icon="folder-open-outline" text={home.document_summary} />
              <StatusLine icon="radio-outline" text={`Sources: ${home.source_stats.enabled || 0}/${home.source_stats.total || 0} enabled`} />
            </View>

            <SectionTitle title="Review Queues" />
            <View style={styles.tileGrid}>
              {[
                ["Top Matches", "top", "star"],
                ["New Jobs", "new", "sparkles"],
                ["Pipeline", "pipeline", "folder"],
                ["Cover Letters", "letters", "create"],
                ["Public Service", "public", "business"],
              ].map(([title, listName, icon]) => (
                <Pressable
                  key={listName}
                  style={styles.tile}
                  onPress={() => navigation.navigate("Jobs", { listName: listName as RootStackParamList["Jobs"]["listName"], title })}
                >
                  <Ionicons name={icon as keyof typeof Ionicons.glyphMap} size={22} color="#2563eb" />
                  <Text style={styles.tileText}>{title}</Text>
                </Pressable>
              ))}
              <Pressable style={styles.tile} onPress={() => navigation.navigate("Documents")}>
                <Ionicons name="cloud-upload-outline" size={22} color="#2563eb" />
                <Text style={styles.tileText}>Documents</Text>
              </Pressable>
              <Pressable style={styles.tile} onPress={() => navigation.navigate("Sources")}>
                <Ionicons name="options-outline" size={22} color="#2563eb" />
                <Text style={styles.tileText}>Sources</Text>
              </Pressable>
            </View>
          </>
        ) : null}
      </ScrollView>
    </Shell>
  );
}

function JobsScreen({ api }: { api: JobBlastApi }) {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const route = useRoute<RouteProp<RootStackParamList, "Jobs">>();
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setJobs((await api.jobs(route.params.listName)).jobs);
    } catch (err) {
      Alert.alert("Could not load jobs", err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [api, route.params.listName]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  return (
    <Shell>
      <ScrollView refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />} contentContainerStyle={styles.content}>
        <SectionTitle title={route.params.title} />
        {jobs.length === 0 && !loading ? <Text style={styles.emptyText}>No jobs in this view.</Text> : null}
        {jobs.map((job) => (
          <JobRow key={job.id} job={job} onPress={() => navigation.navigate("JobDetail", { jobId: job.id })} />
        ))}
      </ScrollView>
    </Shell>
  );
}

function JobDetailScreen({ api }: { api: JobBlastApi }) {
  const route = useRoute<RouteProp<RootStackParamList, "JobDetail">>();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setJob((await api.job(route.params.jobId)).job);
    } catch (err) {
      Alert.alert("Could not load job", err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }, [api, route.params.jobId]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const runAction = async (action: "save" | "skip" | "mark-applied") => {
    try {
      setJob((await api.action(route.params.jobId, action)).job);
    } catch (err) {
      Alert.alert("Action failed", err instanceof Error ? err.message : "Unknown error");
    }
  };

  if (!job) {
    return (
      <Shell>
        <View style={styles.centered}>{loading ? <ActivityIndicator color="#2563eb" /> : <Text style={styles.emptyText}>Job not found.</Text>}</View>
      </Shell>
    );
  }

  const applyUrl = job.apply_url || job.listing_url || "";

  return (
    <Shell>
      <ScrollView contentContainerStyle={styles.content}>
        <View style={styles.detailHeader}>
          <Text style={styles.detailTitle}>{job.title || "Untitled role"}</Text>
          <Text style={styles.detailMeta}>
            {job.company || "Unknown"} | {job.location || "Not stated"}
          </Text>
          <View style={styles.detailScoreRow}>
            <Metric label="Score" value={job.fit_score || 0} />
            <Metric label="State" value={job.state || "matched"} />
          </View>
        </View>

        <View style={styles.actionGrid}>
          {applyUrl ? <IconButton icon="open-outline" label="Apply Link" onPress={() => Linking.openURL(applyUrl)} /> : null}
          <IconButton icon="bookmark-outline" label="Save" variant="secondary" onPress={() => runAction("save")} />
          <IconButton icon="checkmark-done-outline" label="Mark Applied" variant="secondary" onPress={() => runAction("mark-applied")} />
          <IconButton icon="close-circle-outline" label="Skip" variant="danger" onPress={() => runAction("skip")} />
        </View>

        <SectionTitle title="Preparation" />
        <View style={styles.actionGrid}>
          <IconButton icon="create-outline" label="Cover Letter" onPress={() => navigation.navigate("Generated", { jobId: job.id, kind: "cover-letter", title: "Cover Letter" })} />
          <IconButton icon="chatbox-ellipses-outline" label="Answers" variant="secondary" onPress={() => navigation.navigate("Generated", { jobId: job.id, kind: "answers", title: "Answers" })} />
          <IconButton icon="analytics-outline" label="Gap Analysis" variant="secondary" onPress={() => navigation.navigate("Generated", { jobId: job.id, kind: "gap-analysis", title: "Gap Analysis" })} />
        </View>

        <SectionTitle title="Why it matches" />
        <View style={styles.panel}>{(job.why_match || ["No explanation available."]).map((item) => <Text key={item} style={styles.bullet}>- {item}</Text>)}</View>

        <SectionTitle title="Required documents" />
        <View style={styles.panel}>{(job.required_docs || ["Resume / CV"]).map((item) => <Text key={item} style={styles.bullet}>- {item}</Text>)}</View>

        <SectionTitle title="Next action" />
        <View style={styles.panel}>
          <Text style={styles.paragraph}>{job.recommended_next_action || "Review the vacancy and apply manually."}</Text>
          <Text style={styles.mutedText}>Closing date: {job.closing_date || "Not stated"}</Text>
          <Text style={styles.mutedText}>Source: {job.source_name || "Unknown"} ({job.portal_type || "generic"})</Text>
        </View>
      </ScrollView>
    </Shell>
  );
}

function GeneratedScreen({ api }: { api: JobBlastApi }) {
  const route = useRoute<RouteProp<RootStackParamList, "Generated">>();
  const [text, setText] = useState("");
  const [loading, setLoading] = useState(false);

  useFocusEffect(
    useCallback(() => {
      let active = true;
      setLoading(true);
      api
        .generated(route.params.jobId, route.params.kind)
        .then((payload) => active && setText(payload.text))
        .catch((err) => Alert.alert("Generation failed", err instanceof Error ? err.message : "Unknown error"))
        .finally(() => active && setLoading(false));
      return () => {
        active = false;
      };
    }, [api, route.params.jobId, route.params.kind]),
  );

  return (
    <Shell>
      <ScrollView contentContainerStyle={styles.content}>
        <SectionTitle title={route.params.title} />
        {loading ? <ActivityIndicator color="#2563eb" /> : <Text style={styles.generatedText}>{text}</Text>}
      </ScrollView>
    </Shell>
  );
}

function SettingsScreen({ api }: { api: JobBlastApi }) {
  const [profile, setProfile] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      setProfile((await api.profile()).profile);
    } catch (err) {
      Alert.alert("Could not load settings", err instanceof Error ? err.message : "Unknown error");
    }
  }, [api]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const setField = (field: string, value: string) => setProfile((current) => ({ ...current, [field]: value }));
  const setListField = (field: string, value: string) => setProfile((current) => ({ ...current, [field]: value.split(",").map((item) => item.trim()).filter(Boolean) }));

  const save = async () => {
    setSaving(true);
    try {
      setProfile((await api.updateProfile(profile)).profile);
      Alert.alert("Settings saved", "JobBlast profile settings were updated.");
    } catch (err) {
      Alert.alert("Save failed", err instanceof Error ? err.message : "Unknown error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Shell>
      <ScrollView contentContainerStyle={styles.content}>
        <SectionTitle title="Settings" action={<IconButton icon="save-outline" label={saving ? "Saving" : "Save"} onPress={save} />} />
        {[
          ["name", "Name"],
          ["email", "Email"],
          ["phone", "Phone"],
          ["minimum_match_threshold", "Minimum match threshold"],
        ].map(([field, label]) => (
          <View style={styles.formGroup} key={field}>
            <Text style={styles.label}>{label}</Text>
            <TextInput value={String(profile[field] ?? "")} onChangeText={(value) => setField(field, value)} style={styles.input} />
          </View>
        ))}
        {[
          ["target_roles", "Target roles"],
          ["preferred_locations", "Preferred locations"],
          ["keywords", "Keywords"],
          ["excluded_keywords", "Excluded keywords"],
        ].map(([field, label]) => (
          <View style={styles.formGroup} key={field}>
            <Text style={styles.label}>{label}</Text>
            <TextInput
              value={Array.isArray(profile[field]) ? (profile[field] as unknown[]).join(", ") : ""}
              onChangeText={(value) => setListField(field, value)}
              style={styles.input}
            />
          </View>
        ))}
        <View style={styles.formGroup}>
          <Text style={styles.label}>Resume summary</Text>
          <TextInput
            value={String(profile.resume_summary ?? "")}
            onChangeText={(value) => setField("resume_summary", value)}
            style={[styles.input, styles.textArea]}
            multiline
          />
        </View>
      </ScrollView>
    </Shell>
  );
}

function DocumentsScreen({ api }: { api: JobBlastApi }) {
  const [resumeStatus, setResumeStatus] = useState("");
  const [summary, setSummary] = useState("");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);

  const load = useCallback(async () => {
    try {
      const payload = await api.documents();
      setResumeStatus(payload.resume_status);
      setSummary(payload.document_summary);
      setDocuments(payload.documents);
    } catch (err) {
      Alert.alert("Could not load documents", err instanceof Error ? err.message : "Unknown error");
    }
  }, [api]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const pick = async (target: "/v1/resume" | "/v1/documents") => {
    const result = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true });
    if (result.canceled || !result.assets[0]) return;
    const asset = result.assets[0];
    try {
      await api.upload(target, { uri: asset.uri, name: asset.name, mimeType: asset.mimeType });
      await load();
    } catch (err) {
      Alert.alert("Upload failed", err instanceof Error ? err.message : "Unknown error");
    }
  };

  return (
    <Shell>
      <ScrollView contentContainerStyle={styles.content}>
        <SectionTitle title="Resume and Documents" />
        <View style={styles.panel}>
          <StatusLine icon="document-text-outline" text={`Resume: ${resumeStatus}`} />
          <StatusLine icon="folder-open-outline" text={summary} />
        </View>
        <View style={styles.actionGrid}>
          <IconButton icon="cloud-upload-outline" label="Upload Resume" onPress={() => pick("/v1/resume")} />
          <IconButton icon="add-circle-outline" label="Upload Document" variant="secondary" onPress={() => pick("/v1/documents")} />
        </View>
        <SectionTitle title="Stored Documents" />
        {documents.length === 0 ? <Text style={styles.emptyText}>No supporting documents uploaded yet.</Text> : null}
        {documents.map((doc) => (
          <View key={doc.id} style={styles.documentRow}>
            <Ionicons name={doc.active === false ? "ellipse-outline" : "checkmark-circle"} size={22} color={doc.active === false ? "#94a3b8" : "#16a34a"} />
            <View style={styles.jobBody}>
              <Text style={styles.jobTitle}>{doc.original_name || doc.id}</Text>
              <Text style={styles.jobMeta}>{doc.doc_type || "supporting_doc"}</Text>
            </View>
          </View>
        ))}
      </ScrollView>
    </Shell>
  );
}

function SourcesScreen({ api }: { api: JobBlastApi }) {
  const [sources, setSources] = useState<Source[]>([]);

  const load = useCallback(async () => {
    try {
      setSources((await api.sources()).sources);
    } catch (err) {
      Alert.alert("Could not load sources", err instanceof Error ? err.message : "Unknown error");
    }
  }, [api]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const toggle = async (source: Source) => {
    try {
      await api.updateSource(source.id, !source.enabled);
      await load();
    } catch (err) {
      Alert.alert("Update failed", err instanceof Error ? err.message : "Unknown error");
    }
  };

  return (
    <Shell>
      <ScrollView contentContainerStyle={styles.content}>
        <SectionTitle title="Sources" />
        {sources.map((source) => (
          <Pressable key={source.id} style={styles.sourceRow} onPress={() => toggle(source)}>
            <Ionicons name={source.enabled === false ? "toggle-outline" : "toggle"} size={30} color={source.enabled === false ? "#94a3b8" : "#2563eb"} />
            <View style={styles.jobBody}>
              <Text style={styles.jobTitle}>{source.name || source.id}</Text>
              <Text style={styles.jobMeta}>
                {source.scan_mode || "discovery"} | {source.last_result || "never scanned"}
              </Text>
              <Text style={styles.mutedText}>Scans {source.scan_count || 0} | OK {source.success_count || 0} | Warn {source.warning_count || 0} | Err {source.error_count || 0}</Text>
            </View>
          </Pressable>
        ))}
      </ScrollView>
    </Shell>
  );
}

function ConnectionScreen({ settings, setSettings }: { settings: ApiSettings; setSettings: (settings: ApiSettings) => void }) {
  const [baseUrl, setBaseUrl] = useState(settings.baseUrl);
  const [token, setToken] = useState(settings.token);

  return (
    <Shell>
      <ScrollView contentContainerStyle={styles.content}>
        <SectionTitle title="Connection" />
        <Text style={styles.paragraph}>For Android emulator use http://10.0.2.2:8000. For a physical phone, use your computer's local network IP and run the API on port 8000.</Text>
        <View style={styles.formGroup}>
          <Text style={styles.label}>API base URL</Text>
          <TextInput autoCapitalize="none" value={baseUrl} onChangeText={setBaseUrl} style={styles.input} />
        </View>
        <View style={styles.formGroup}>
          <Text style={styles.label}>API token</Text>
          <TextInput autoCapitalize="none" value={token} onChangeText={setToken} style={styles.input} secureTextEntry />
        </View>
        <IconButton icon="save-outline" label="Use Connection" onPress={() => setSettings({ baseUrl, token })} />
      </ScrollView>
    </Shell>
  );
}

export default function App() {
  const [settings, setSettings] = useState(DEFAULT_SETTINGS);
  const api = useApi(settings);

  return (
    <NavigationContainer>
      <StatusBar style="dark" />
      <Stack.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: "#f8fafc" },
          headerTitleStyle: { color: "#0f172a", fontWeight: "800" },
          headerTintColor: "#0f172a",
          contentStyle: { backgroundColor: "#f8fafc" },
        }}
      >
        <Stack.Screen name="Home" options={{ title: "JobBlast" }}>
          {() => <HomeScreen api={api} />}
        </Stack.Screen>
        <Stack.Screen name="Jobs" options={({ route }) => ({ title: route.params.title })}>
          {() => <JobsScreen api={api} />}
        </Stack.Screen>
        <Stack.Screen name="JobDetail" options={{ title: "Job Detail" }}>
          {() => <JobDetailScreen api={api} />}
        </Stack.Screen>
        <Stack.Screen name="Generated" options={({ route }) => ({ title: route.params.title })}>
          {() => <GeneratedScreen api={api} />}
        </Stack.Screen>
        <Stack.Screen name="Settings" options={{ title: "Settings" }}>
          {() => <SettingsScreen api={api} />}
        </Stack.Screen>
        <Stack.Screen name="Documents" options={{ title: "Documents" }}>
          {() => <DocumentsScreen api={api} />}
        </Stack.Screen>
        <Stack.Screen name="Sources" options={{ title: "Sources" }}>
          {() => <SourcesScreen api={api} />}
        </Stack.Screen>
        <Stack.Screen name="Connection" options={{ title: "Connection" }}>
          {() => <ConnectionScreen settings={settings} setSettings={setSettings} />}
        </Stack.Screen>
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  shell: {
    flex: 1,
    backgroundColor: "#f8fafc",
  },
  content: {
    padding: 18,
    gap: 16,
  },
  hero: {
    backgroundColor: "#0f172a",
    borderRadius: 8,
    padding: 22,
    gap: 14,
  },
  brandMark: {
    width: 48,
    height: 48,
    borderRadius: 8,
    backgroundColor: "#2563eb",
    alignItems: "center",
    justifyContent: "center",
  },
  heroTitle: {
    color: "#ffffff",
    fontSize: 34,
    lineHeight: 40,
    fontWeight: "900",
  },
  heroText: {
    color: "#cbd5e1",
    fontSize: 16,
    lineHeight: 23,
  },
  heroActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  button: {
    minHeight: 46,
    borderRadius: 8,
    paddingHorizontal: 14,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
  },
  primaryButton: {
    backgroundColor: "#2563eb",
  },
  secondaryButton: {
    backgroundColor: "#e2e8f0",
  },
  dangerButton: {
    backgroundColor: "#dc2626",
  },
  buttonText: {
    color: "#ffffff",
    fontSize: 14,
    fontWeight: "800",
  },
  secondaryButtonText: {
    color: "#0f172a",
  },
  metricsGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  metric: {
    flexGrow: 1,
    minWidth: "46%",
    backgroundColor: "#ffffff",
    borderColor: "#e2e8f0",
    borderWidth: 1,
    borderRadius: 8,
    padding: 14,
  },
  metricValue: {
    color: "#0f172a",
    fontSize: 23,
    fontWeight: "900",
  },
  metricLabel: {
    color: "#64748b",
    fontSize: 12,
    fontWeight: "700",
    textTransform: "uppercase",
  },
  sectionHeader: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 12,
  },
  sectionTitle: {
    color: "#0f172a",
    fontSize: 20,
    fontWeight: "900",
  },
  panel: {
    backgroundColor: "#ffffff",
    borderColor: "#e2e8f0",
    borderWidth: 1,
    borderRadius: 8,
    padding: 14,
    gap: 10,
  },
  statusLine: {
    flexDirection: "row",
    gap: 10,
    alignItems: "flex-start",
  },
  statusText: {
    color: "#334155",
    fontSize: 14,
    lineHeight: 20,
    flex: 1,
  },
  tileGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  tile: {
    width: "47.8%",
    minHeight: 86,
    backgroundColor: "#ffffff",
    borderColor: "#e2e8f0",
    borderWidth: 1,
    borderRadius: 8,
    padding: 14,
    justifyContent: "space-between",
  },
  tileText: {
    color: "#0f172a",
    fontSize: 15,
    fontWeight: "800",
  },
  errorBox: {
    backgroundColor: "#fef2f2",
    borderColor: "#fecaca",
    borderWidth: 1,
    borderRadius: 8,
    padding: 14,
  },
  errorTitle: {
    color: "#991b1b",
    fontSize: 15,
    fontWeight: "900",
  },
  errorText: {
    color: "#7f1d1d",
    fontSize: 13,
    lineHeight: 19,
  },
  jobRow: {
    backgroundColor: "#ffffff",
    borderColor: "#e2e8f0",
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  jobScore: {
    width: 58,
    height: 58,
    borderRadius: 8,
    backgroundColor: "#eff6ff",
    alignItems: "center",
    justifyContent: "center",
  },
  scoreValue: {
    color: "#1d4ed8",
    fontSize: 20,
    fontWeight: "900",
  },
  scoreLabel: {
    color: "#2563eb",
    fontSize: 10,
    fontWeight: "800",
    textTransform: "uppercase",
  },
  jobBody: {
    flex: 1,
    gap: 3,
  },
  jobTitle: {
    color: "#0f172a",
    fontSize: 15,
    fontWeight: "900",
  },
  jobMeta: {
    color: "#475569",
    fontSize: 13,
    lineHeight: 18,
  },
  jobState: {
    color: "#2563eb",
    fontSize: 12,
    fontWeight: "800",
  },
  emptyText: {
    color: "#64748b",
    fontSize: 15,
    lineHeight: 22,
  },
  centered: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
  },
  detailHeader: {
    backgroundColor: "#ffffff",
    borderColor: "#e2e8f0",
    borderWidth: 1,
    borderRadius: 8,
    padding: 16,
    gap: 10,
  },
  detailTitle: {
    color: "#0f172a",
    fontSize: 26,
    lineHeight: 32,
    fontWeight: "900",
  },
  detailMeta: {
    color: "#475569",
    fontSize: 15,
    lineHeight: 21,
  },
  detailScoreRow: {
    flexDirection: "row",
    gap: 10,
  },
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
  },
  bullet: {
    color: "#334155",
    fontSize: 14,
    lineHeight: 21,
  },
  paragraph: {
    color: "#334155",
    fontSize: 15,
    lineHeight: 23,
  },
  mutedText: {
    color: "#64748b",
    fontSize: 12,
    lineHeight: 18,
  },
  generatedText: {
    color: "#1e293b",
    backgroundColor: "#ffffff",
    borderColor: "#e2e8f0",
    borderWidth: 1,
    borderRadius: 8,
    padding: 16,
    fontSize: 15,
    lineHeight: 24,
  },
  formGroup: {
    gap: 7,
  },
  label: {
    color: "#334155",
    fontSize: 13,
    fontWeight: "800",
  },
  input: {
    minHeight: 46,
    borderRadius: 8,
    borderColor: "#cbd5e1",
    borderWidth: 1,
    backgroundColor: "#ffffff",
    paddingHorizontal: 12,
    color: "#0f172a",
    fontSize: 15,
  },
  textArea: {
    minHeight: 128,
    paddingTop: 12,
    textAlignVertical: "top",
  },
  documentRow: {
    backgroundColor: "#ffffff",
    borderColor: "#e2e8f0",
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
  sourceRow: {
    backgroundColor: "#ffffff",
    borderColor: "#e2e8f0",
    borderWidth: 1,
    borderRadius: 8,
    padding: 12,
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
  },
});
