// Shapes returned by packages/app/server.py. Kept in one place so a backend
// change has exactly one file to update on this side.

export type BookingType = "flight" | "train" | "hotel" | "appointment" | "ride" | "restaurant";
export type Outcome = "success" | "refused" | "awaiting_confirmation" | "failure" | "error";
export type TrustLevel = "verified" | "failed" | "unknown";

export interface CalEvent {
  id: string;
  title: string;
  start: string;
  end: string | null;
  type: BookingType | null;
  ref: string | null;
  location: string | null;
  alarm: string | null;
  /** The type was read off the title (an event Gmail or you created). */
  type_guessed?: boolean;
}

export interface Trust {
  level: TrustLevel;
  domain: string;
  vendor_id: string | null;
  vendor_name: string | null;
  reason: string;
}

export interface ExtractedBooking {
  status: string | null;
  type: BookingType | null;
  ref: string | null;
  title: string | null;
  start: string | null;
  end: string | null;
  location: string | null;
  reason: string | null;
}

export interface Confirmation {
  request_id: string;
  status: "pending" | "queued";
  prompt: string;
  action: "DELETE" | "UPDATE" | "BOOK";
  created_at: string;
  timeout_at: string;
  seconds_left: number;
  before: CalEvent | null;
  after: CalEvent | null;
  source: "email" | "voice" | "swiggy" | null;
  meta: {
    kind?: string;
    guests?: number;
    stage?: string;
    vendor_id?: string | null;
    trust?: Trust;
    subject?: string;
    sender?: string;
    channel?: string;
  };
}

export interface MailRow {
  id: number;
  message_id: string;
  idx: number;
  channel: string;
  received_at: string | null;
  processed_at: string;
  sender: string | null;
  subject: string | null;
  stage: string | null;
  vendor_id: string | null;
  trust: Trust | null;
  status: Outcome;
  message: string | null;
  booking: ExtractedBooking | null;
  request_id: string | null;
  dismissed: boolean;
}

export interface ActivityRow {
  id: number;
  ts: string;
  channel: "app" | "telegram" | "gmail" | "imap" | "demo" | "upload" | "system" | "swiggy";
  kind: string;
  input: string | null;
  status: Outcome;
  message: string | null;
  meta: Record<string, unknown> | null;
  trajectory_id: string | null;
  request_id: string | null;
}

export interface Score {
  value: number | null;
  scale: string;
  rationale: string;
  applicable: boolean;
  error?: string | null;
}

export interface Trajectory {
  trajectory_id: string;
  agent: { model: string };
  task: { source: string; input: string };
  reasoning: { step: number; text: string }[] | null;
  tool_calls: { name: string; args: Record<string, unknown>; result: unknown; ok: boolean }[];
  actions: { type: string; target: string | null; destructive: boolean; confirmed_by_user: boolean | null }[];
  outcome: { status: Outcome; user_visible: string };
  scores: Record<string, Score> | null;
}

export interface ConnectionState {
  ok: boolean;
  provider?: string | null;
  detail?: string;
}

export interface Status {
  mode: "demo" | "live";
  ready: boolean;
  connections: {
    calendar: ConnectionState;
    mail: ConnectionState;
    telegram: ConnectionState;
    ollama: ConnectionState;
    voice: ConnectionState;
    swiggy: ConnectionState;
  };
  google: { configured: boolean; connected: boolean; scopes?: string[]; redirect_uri?: string };
  swiggy: { connected: boolean; redirect_uri?: string };
  mail_channel: string;
  last_scan: { at: string; handled: number } | null;
  settings: {
    mode: "demo" | "live";
    base_url: string;
    redirect_uri: string;
    zone: string;
    active_window: string;
    poll_minutes: number;
    alarm_minutes: number;
    calendar_id: string;
    gmail_query: string;
    google_client_id_set: boolean;
    google_client_secret_set: boolean;
    imap_host: string;
    imap_user: string;
    imap_password_set: boolean;
    telegram_token_set: boolean;
    telegram_chat_id: string;
    ollama_host: string;
    ollama_model: string;
    whisper_model: string;
    notify_changes: boolean;
    dining_area: string;
    dining_pin: string;
    home: string;
  };
  ingest: { stats: { received: number; accepted: number; quarantined: number }; stored: number };
}

export interface BookingHistory {
  at: string;
  type: "CREATE" | "UPDATE" | "DELETE";
  channel: string;
  message: string;
}

/** GET /api/events/{id}: the calendar event plus everything Tripwire knows. */
export interface EventDetail {
  event: CalEvent | null;
  /** The event is no longer on the calendar (deleted). */
  gone: boolean;
  google: {
    html_link?: string | null;
    description?: string;
    origin?: "gmail" | "tripwire" | "calendar";
    source_url?: string | null;
    source_title?: string | null;
    created?: string | null;
    updated?: string | null;
  };
  booking: {
    booking_type?: string;
    status?: string;
    source?: string;
    booking_ref?: string;
    destination?: string;
    location?: string;
    pickup?: string;
    dropoff?: string;
    provider?: string;
    venue?: string;
    party_size?: number;
    first_seen?: string;
    deleted?: boolean;
    [key: string]: unknown;
  };
  email: {
    message_id?: string;
    subject?: string;
    sender?: string;
    stage?: string;
    vendor_id?: string;
    channel?: string;
    trust?: string;
  } | null;
  history: BookingHistory[];
  links: { calendar?: string; email?: string; uber?: string; swiggy?: string };
  /** A Swiggy table Tripwire booked: Cancel can also cancel it at Swiggy. */
  can_cancel_reservation: boolean;
}

export interface FoodRestaurant {
  id: string | null;
  name: string;
  cuisines: string[];
  rating: number | null;
  ratings: string | null;
  eta: string | null;
  cost: string | null;
  offer: string | null;
  image: string | null;
  area: string | null;
  open: boolean;
  next_open: string | null;
  veg: boolean | null;
  url: string;
}

export interface FoodIdeas {
  query: string;
  area: string;
  restaurants: FoodRestaurant[];
  dishes: { name: string; price: number | null; restaurant: string | null; rating: string | number | null; veg: boolean | null; bestseller: boolean }[];
  dineout: { id: string | null; name: string; area: string | null; rating: string | null; cost: string | null; offers: string[]; deals: string[] }[];
}

export interface Coupon {
  title: string | null;
  subtitle: string | null;
  description: string | null;
  applicable: boolean | null;
  code: string | null;
}

export interface SwiggyOrder {
  id: string;
  title: string | null;
  subtitle: string | null;
  status: string | null;
  eta: string | null;
  progress: string | null;
}

export interface CommandResult {
  status: Outcome;
  message: string;
  needs_clarification: boolean;
  request_id: string | null;
  trajectory_id: string | null;
  meta: Record<string, unknown>;
}
