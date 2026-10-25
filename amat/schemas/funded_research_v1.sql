-- Funded trading autoresearch, schema 1.0.0. SQLite >=3.37, JSON1, foreign_keys ON.
-- This is a new-store schema, not a migration of research.sqlite3.
PRAGMA foreign_keys = ON;
BEGIN;

CREATE TABLE schema_version (
  version TEXT PRIMARY KEY, installed_at_ns INTEGER NOT NULL, ddl_sha256 TEXT NOT NULL
) STRICT;

CREATE TABLE artifact (
  id TEXT PRIMARY KEY, sha256 TEXT NOT NULL CHECK(length(sha256)=64),
  uri TEXT NOT NULL, media_type TEXT NOT NULL, bytes INTEGER NOT NULL CHECK(bytes>=0),
  classification TEXT NOT NULL CHECK(classification IN ('public','internal','restricted')),
  created_at_ns INTEGER NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata_json)),
  UNIQUE(sha256,uri)
) STRICT;

CREATE TABLE campaign (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at_ns INTEGER NOT NULL,
  objective_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  initial_balance_usd_micros INTEGER NOT NULL CHECK(initial_balance_usd_micros>0),
  target_profit_usd_micros INTEGER NOT NULL CHECK(target_profit_usd_micros>0),
  horizon_calendar_days INTEGER NOT NULL CHECK(horizon_calendar_days>0),
  execution_scope TEXT NOT NULL CHECK(execution_scope IN ('research','shadow','alerts_only')),
  max_trials INTEGER NOT NULL CHECK(max_trials>0),
  stopping_policy_json TEXT NOT NULL CHECK(json_valid(stopping_policy_json)),
  created_by TEXT NOT NULL
) STRICT;

CREATE TABLE source_document (
  id TEXT PRIMARY KEY, url TEXT NOT NULL, publisher TEXT NOT NULL,
  retrieved_at_ns INTEGER NOT NULL, effective_at_ns INTEGER,
  artifact_id TEXT NOT NULL REFERENCES artifact(id),
  authority TEXT NOT NULL CHECK(authority IN ('firm_primary','exchange_primary','vendor_primary','paper','third_party'))
) STRICT;

CREATE TABLE rule_snapshot (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id),
  source_document_id TEXT NOT NULL REFERENCES source_document(id),
  firm TEXT NOT NULL, product TEXT NOT NULL, platform TEXT NOT NULL,
  stage TEXT NOT NULL CHECK(stage IN ('evaluation','performance','live_program')),
  effective_from_ns INTEGER NOT NULL, effective_to_ns INTEGER,
  rules_sha256 TEXT NOT NULL CHECK(length(rules_sha256)=64),
  initial_balance_usd_micros INTEGER NOT NULL CHECK(initial_balance_usd_micros>0),
  target_profit_usd_micros INTEGER CHECK(target_profit_usd_micros>0),
  horizon_calendar_days INTEGER CHECK(horizon_calendar_days>0),
  drawdown_usd_micros INTEGER NOT NULL CHECK(drawdown_usd_micros>0),
  trail_mode TEXT NOT NULL CHECK(trail_mode IN ('static','eod','intraday')),
  trail_cap_equity_usd_micros INTEGER,
  daily_loss_usd_micros INTEGER CHECK(daily_loss_usd_micros>0),
  max_mini_equivalents INTEGER NOT NULL CHECK(max_mini_equivalents>0),
  micro_per_mini INTEGER NOT NULL CHECK(micro_per_mini>0),
  flatten_local_time TEXT NOT NULL, timezone TEXT NOT NULL,
  automation_policy TEXT NOT NULL CHECK(automation_policy IN ('manual_only','alerts_only','automated_allowed','unknown')),
  payout_rules_json TEXT NOT NULL CHECK(json_valid(payout_rules_json)),
  inactivity_rules_json TEXT NOT NULL CHECK(json_valid(inactivity_rules_json)),
  scaling_rules_json TEXT NOT NULL CHECK(json_valid(scaling_rules_json)),
  exceptions_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(exceptions_json)),
  CHECK(effective_to_ns IS NULL OR effective_to_ns>effective_from_ns)
) STRICT;

CREATE TABLE instrument (
  id TEXT PRIMARY KEY, root_symbol TEXT NOT NULL, exchange TEXT NOT NULL,
  currency TEXT NOT NULL CHECK(currency='USD'),
  tick_size_numerator INTEGER NOT NULL CHECK(tick_size_numerator>0),
  tick_size_denominator INTEGER NOT NULL CHECK(tick_size_denominator>0),
  tick_value_usd_micros INTEGER NOT NULL CHECK(tick_value_usd_micros>0),
  definition_source_id TEXT NOT NULL REFERENCES source_document(id),
  specification_sha256 TEXT NOT NULL CHECK(length(specification_sha256)=64)
) STRICT;

CREATE TABLE contract (
  id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL REFERENCES instrument(id),
  exchange_symbol TEXT NOT NULL, vendor_instrument_id TEXT NOT NULL,
  first_trade_at_ns INTEGER, last_trade_at_ns INTEGER NOT NULL,
  expiry_date TEXT NOT NULL, definition_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  UNIQUE(instrument_id,exchange_symbol)
) STRICT;

CREATE TABLE calendar_snapshot (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, timezone TEXT NOT NULL,
  artifact_id TEXT NOT NULL REFERENCES artifact(id),
  start_date TEXT NOT NULL, end_date TEXT NOT NULL,
  quality TEXT NOT NULL CHECK(quality IN ('exchange_verified','vendor_verified','proxy','unverified')),
  CHECK(start_date<=end_date)
) STRICT;

CREATE TABLE session (
  id TEXT PRIMARY KEY, calendar_id TEXT NOT NULL REFERENCES calendar_snapshot(id),
  trading_date TEXT NOT NULL, open_at_ns INTEGER NOT NULL, close_at_ns INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('full','early_close','holiday','halted')),
  segments_json TEXT NOT NULL CHECK(json_valid(segments_json)),
  UNIQUE(calendar_id,trading_date), CHECK(open_at_ns<=close_at_ns)
) STRICT;

CREATE TABLE dataset_snapshot (
  id TEXT PRIMARY KEY, instrument_id TEXT NOT NULL REFERENCES instrument(id),
  calendar_id TEXT NOT NULL REFERENCES calendar_snapshot(id),
  manifest_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  provider TEXT NOT NULL, acquired_at_ns INTEGER NOT NULL,
  start_at_ns INTEGER NOT NULL, end_at_ns INTEGER NOT NULL,
  timestamp_semantics TEXT NOT NULL CHECK(timestamp_semantics IN ('bar_start','bar_end','event_time','unknown')),
  price_kind TEXT NOT NULL CHECK(price_kind IN ('raw_contract','continuous_unadjusted','continuous_adjusted','proxy','unknown')),
  contract_identity_quality TEXT NOT NULL CHECK(contract_identity_quality IN ('verified','inferred','missing')),
  license_ref TEXT NOT NULL,
  promotion_eligible INTEGER NOT NULL CHECK(promotion_eligible IN (0,1)),
  CHECK(start_at_ns<end_at_ns),
  CHECK(promotion_eligible=0 OR (price_kind='raw_contract' AND contract_identity_quality='verified' AND timestamp_semantics!='unknown'))
) STRICT;

CREATE TABLE dataset_partition (
  id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES dataset_snapshot(id),
  contract_id TEXT REFERENCES contract(id), session_id TEXT REFERENCES session(id),
  kind TEXT NOT NULL CHECK(kind IN ('bars','trades','bbo','depth','definitions','events')),
  artifact_id TEXT NOT NULL REFERENCES artifact(id), row_count INTEGER NOT NULL CHECK(row_count>=0),
  start_at_ns INTEGER NOT NULL, end_at_ns INTEGER NOT NULL,
  resolution_ns INTEGER CHECK(resolution_ns>0),
  CHECK(start_at_ns<=end_at_ns)
) STRICT;

CREATE TABLE roll_assignment (
  dataset_id TEXT NOT NULL REFERENCES dataset_snapshot(id),
  session_id TEXT NOT NULL REFERENCES session(id), contract_id TEXT NOT NULL REFERENCES contract(id),
  policy_artifact_id TEXT NOT NULL REFERENCES artifact(id), selected_at_ns INTEGER NOT NULL,
  selection_inputs_available_at_ns INTEGER NOT NULL,
  PRIMARY KEY(dataset_id,session_id), CHECK(selection_inputs_available_at_ns<=selected_at_ns)
) STRICT;

CREATE TABLE data_quality_check (
  id TEXT PRIMARY KEY, dataset_id TEXT NOT NULL REFERENCES dataset_snapshot(id),
  partition_id TEXT REFERENCES dataset_partition(id), check_name TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('pass','fail','unknown','not_applicable')),
  severity TEXT NOT NULL CHECK(severity IN ('info','warning','blocker')),
  observed_json TEXT NOT NULL CHECK(json_valid(observed_json)),
  expected_json TEXT NOT NULL CHECK(json_valid(expected_json)),
  evidence_artifact_id TEXT REFERENCES artifact(id), checked_at_ns INTEGER NOT NULL,
  checker_code_sha256 TEXT NOT NULL CHECK(length(checker_code_sha256)=64)
) STRICT;

CREATE TABLE split_plan (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id),
  dataset_id TEXT NOT NULL REFERENCES dataset_snapshot(id), frozen_at_ns INTEGER NOT NULL,
  manifest_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  purge_ns INTEGER NOT NULL CHECK(purge_ns>=0), embargo_ns INTEGER NOT NULL CHECK(embargo_ns>=0),
  exposure_status TEXT NOT NULL CHECK(exposure_status IN ('unseen','previously_researched','unknown')),
  max_holdout_queries INTEGER NOT NULL CHECK(max_holdout_queries>=0)
) STRICT;

CREATE TABLE split_segment (
  id TEXT PRIMARY KEY, split_plan_id TEXT NOT NULL REFERENCES split_plan(id), fold INTEGER NOT NULL CHECK(fold>=0),
  role TEXT NOT NULL CHECK(role IN ('train','validation','holdout','forward','exploratory')),
  start_at_ns INTEGER NOT NULL, end_at_ns INTEGER NOT NULL, CHECK(start_at_ns<end_at_ns),
  UNIQUE(split_plan_id,fold,role)
) STRICT;

CREATE TABLE data_exposure (
  id TEXT PRIMARY KEY, split_segment_id TEXT NOT NULL REFERENCES split_segment(id),
  actor TEXT NOT NULL, purpose TEXT NOT NULL, exposed_at_ns INTEGER NOT NULL,
  artifact_id TEXT REFERENCES artifact(id)
) STRICT;

CREATE TABLE feature_definition (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, code_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  parameters_json TEXT NOT NULL CHECK(json_valid(parameters_json)),
  lookback_ns INTEGER NOT NULL CHECK(lookback_ns>=0), availability_lag_ns INTEGER NOT NULL CHECK(availability_lag_ns>=0),
  causal_test_artifact_id TEXT NOT NULL REFERENCES artifact(id)
) STRICT;

CREATE TABLE feature_partition (
  id TEXT PRIMARY KEY, feature_definition_id TEXT NOT NULL REFERENCES feature_definition(id),
  input_partition_id TEXT NOT NULL REFERENCES dataset_partition(id),
  artifact_id TEXT NOT NULL REFERENCES artifact(id),
  dependency_sha256 TEXT NOT NULL CHECK(length(dependency_sha256)=64),
  UNIQUE(feature_definition_id,input_partition_id,dependency_sha256)
) STRICT;

CREATE TABLE hypothesis (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id),
  parent_hypothesis_id TEXT REFERENCES hypothesis(id), mechanism TEXT NOT NULL,
  expected_effect TEXT NOT NULL, falsification_test TEXT NOT NULL,
  evidence_artifact_id TEXT REFERENCES artifact(id),
  registered_at_ns INTEGER NOT NULL, proposed_by TEXT NOT NULL,
  expected_information_gain REAL CHECK(expected_information_gain>=0),
  expected_compute_seconds REAL CHECK(expected_compute_seconds>=0),
  priority_reason TEXT NOT NULL
) STRICT;

CREATE TABLE candidate (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id),
  hypothesis_id TEXT NOT NULL REFERENCES hypothesis(id), parent_candidate_id TEXT REFERENCES candidate(id),
  family TEXT NOT NULL, spec_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  spec_sha256 TEXT NOT NULL CHECK(length(spec_sha256)=64), code_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  parameter_count INTEGER NOT NULL CHECK(parameter_count>=0), complexity_score REAL NOT NULL CHECK(complexity_score>=0),
  registered_at_ns INTEGER NOT NULL, UNIQUE(campaign_id,spec_sha256)
) STRICT;

CREATE TABLE candidate_change (
  candidate_id TEXT NOT NULL REFERENCES candidate(id), ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  field_path TEXT NOT NULL, before_json TEXT NOT NULL CHECK(json_valid(before_json)),
  after_json TEXT NOT NULL CHECK(json_valid(after_json)), rationale TEXT NOT NULL,
  PRIMARY KEY(candidate_id,ordinal)
) STRICT;

CREATE TABLE cost_scenario (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id), name TEXT NOT NULL,
  commission_per_contract_side_usd_micros INTEGER NOT NULL CHECK(commission_per_contract_side_usd_micros>=0),
  spread_ticks REAL NOT NULL CHECK(spread_ticks>=0), impact_ticks REAL NOT NULL CHECK(impact_ticks>=0),
  exit_gap_policy TEXT NOT NULL, calibration_artifact_id TEXT REFERENCES artifact(id),
  provenance TEXT NOT NULL CHECK(provenance IN ('measured','vendor_schedule','assumed','mixed')),
  effective_at_ns INTEGER NOT NULL, includes_exchange_fees INTEGER NOT NULL CHECK(includes_exchange_fees IN (0,1)),
  spec_sha256 TEXT NOT NULL CHECK(length(spec_sha256)=64)
) STRICT;

CREATE TABLE execution_model (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id),
  code_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  decision_clock TEXT NOT NULL, fill_clock TEXT NOT NULL,
  latency_ns INTEGER NOT NULL CHECK(latency_ns>=0),
  fill_basis TEXT NOT NULL CHECK(fill_basis IN ('next_open','bid_ask','trade_tick','queue_model')),
  intrabar_policy TEXT NOT NULL CHECK(intrabar_policy IN ('tick_order','adverse_first','favorable_then_adverse','bounds')),
  missing_data_policy TEXT NOT NULL CHECK(missing_data_policy IN ('abstain','invalidate_window','fail_run')),
  risk_policy_json TEXT NOT NULL CHECK(json_valid(risk_policy_json)),
  spec_sha256 TEXT NOT NULL CHECK(length(spec_sha256)=64)
) STRICT;

CREATE TABLE resource_budget (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id),
  category TEXT NOT NULL CHECK(category IN ('tokens','compute_seconds','usd_micros','trials','holdout_queries')),
  limit_amount INTEGER NOT NULL CHECK(limit_amount>=0),
  period_start_ns INTEGER NOT NULL, period_end_ns INTEGER NOT NULL,
  CHECK(period_start_ns<period_end_ns)
) STRICT;

CREATE TABLE budget_reservation (
  id TEXT PRIMARY KEY, budget_id TEXT NOT NULL REFERENCES resource_budget(id),
  task_ref TEXT NOT NULL, reserved_amount INTEGER NOT NULL CHECK(reserved_amount>=0),
  created_at_ns INTEGER NOT NULL, UNIQUE(budget_id,task_ref)
) STRICT;

CREATE TABLE budget_settlement (
  reservation_id TEXT PRIMARY KEY REFERENCES budget_reservation(id),
  actual_amount INTEGER CHECK(actual_amount>=0),
  status TEXT NOT NULL CHECK(status IN ('measured','released','unknown')),
  settled_at_ns INTEGER NOT NULL,
  CHECK((status='measured' AND actual_amount IS NOT NULL) OR (status='released' AND actual_amount=0) OR (status='unknown' AND actual_amount IS NULL))
) STRICT;

CREATE TABLE model_task (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id),
  kind TEXT NOT NULL CHECK(kind IN ('hypothesis','design_review','code_review','failure_analysis','selection_review')),
  requested_model TEXT NOT NULL CHECK(requested_model GLOB 'gpt-6*'),
  reasoning_effort TEXT NOT NULL, input_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  output_schema_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  max_output_tokens INTEGER NOT NULL CHECK(max_output_tokens>0),
  timeout_seconds INTEGER NOT NULL CHECK(timeout_seconds>0),
  request_sha256 TEXT NOT NULL CHECK(length(request_sha256)=64), created_at_ns INTEGER NOT NULL
) STRICT;

CREATE TABLE model_attempt (
  id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES model_task(id),
  attempt INTEGER NOT NULL CHECK(attempt>0), requested_model TEXT NOT NULL, served_model TEXT,
  started_at_ns INTEGER NOT NULL, finished_at_ns INTEGER,
  status TEXT NOT NULL CHECK(status IN ('succeeded','rate_limited','quota','timeout','auth','invalid_output','interrupted','failed')),
  input_tokens INTEGER CHECK(input_tokens>=0), output_tokens INTEGER CHECK(output_tokens>=0),
  billed_usd_micros INTEGER CHECK(billed_usd_micros>=0), pricing_source_id TEXT REFERENCES source_document(id),
  retry_after_ns INTEGER, output_artifact_id TEXT REFERENCES artifact(id),
  sanitized_error_code TEXT, UNIQUE(task_id,attempt),
  CHECK(status!='succeeded' OR output_artifact_id IS NOT NULL)
) STRICT;

CREATE TABLE experiment (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id),
  candidate_id TEXT NOT NULL REFERENCES candidate(id), split_plan_id TEXT NOT NULL REFERENCES split_plan(id),
  rule_snapshot_id TEXT NOT NULL REFERENCES rule_snapshot(id),
  cost_scenario_id TEXT NOT NULL REFERENCES cost_scenario(id),
  execution_model_id TEXT NOT NULL REFERENCES execution_model(id),
  baseline_experiment_id TEXT REFERENCES experiment(id),
  purpose TEXT NOT NULL CHECK(purpose IN ('screen','ablation','walk_forward','stress','holdout','replication','forward')),
  trial_number INTEGER NOT NULL CHECK(trial_number>0), seed INTEGER NOT NULL,
  dependency_sha256 TEXT NOT NULL CHECK(length(dependency_sha256)=64),
  registered_at_ns INTEGER NOT NULL, UNIQUE(campaign_id,trial_number), UNIQUE(campaign_id,dependency_sha256)
) STRICT;

CREATE TABLE experiment_run (
  id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL REFERENCES experiment(id), attempt INTEGER NOT NULL CHECK(attempt>0),
  worker_id TEXT NOT NULL, started_at_ns INTEGER NOT NULL, finished_at_ns INTEGER,
  status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed','cancelled','interrupted','blocked')),
  engine_sha256 TEXT NOT NULL CHECK(length(engine_sha256)=64), environment_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  cpu_seconds REAL CHECK(cpu_seconds>=0), peak_memory_bytes INTEGER CHECK(peak_memory_bytes>=0),
  result_artifact_id TEXT REFERENCES artifact(id), sanitized_error_code TEXT,
  UNIQUE(experiment_id,attempt), CHECK(finished_at_ns IS NULL OR finished_at_ns>=started_at_ns),
  CHECK(status!='completed' OR result_artifact_id IS NOT NULL)
) STRICT;
CREATE UNIQUE INDEX one_running_attempt ON experiment_run(experiment_id) WHERE status='running';

CREATE TABLE evaluation_window (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES experiment_run(id), split_segment_id TEXT NOT NULL REFERENCES split_segment(id),
  start_at_ns INTEGER NOT NULL, deadline_at_ns INTEGER NOT NULL,
  outcome TEXT NOT NULL CHECK(outcome IN ('passed','failed','expired','invalid','incomplete')),
  ended_at_ns INTEGER NOT NULL, dependence_group TEXT NOT NULL,
  reset_seed INTEGER NOT NULL, starting_equity_usd_micros INTEGER NOT NULL,
  ending_equity_usd_micros INTEGER NOT NULL, net_pnl_usd_micros INTEGER NOT NULL,
  eligible_sessions INTEGER NOT NULL CHECK(eligible_sessions>=0), observed_sessions INTEGER NOT NULL CHECK(observed_sessions>=0),
  missed_sessions INTEGER NOT NULL CHECK(missed_sessions>=0), trade_count INTEGER NOT NULL CHECK(trade_count>=0),
  outcome_evidence_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  CHECK(start_at_ns<deadline_at_ns), CHECK(ended_at_ns>=start_at_ns),
  CHECK(net_pnl_usd_micros=ending_equity_usd_micros-starting_equity_usd_micros)
) STRICT;

CREATE TABLE account_event (
  id TEXT PRIMARY KEY, window_id TEXT NOT NULL REFERENCES evaluation_window(id), sequence INTEGER NOT NULL CHECK(sequence>=0),
  event_at_ns INTEGER NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('reset','mark','floor_update','daily_pause','risk_exhausted','breach','target','expiry','fee','reward')),
  cash_usd_micros INTEGER NOT NULL, unrealized_usd_micros INTEGER NOT NULL,
  equity_usd_micros INTEGER NOT NULL, peak_equity_usd_micros INTEGER NOT NULL,
  floor_usd_micros INTEGER NOT NULL, remaining_capacity_usd_micros INTEGER NOT NULL,
  daily_pnl_usd_micros INTEGER NOT NULL, payload_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload_json)),
  UNIQUE(window_id,sequence), CHECK(equity_usd_micros=cash_usd_micros+unrealized_usd_micros),
  CHECK(remaining_capacity_usd_micros=equity_usd_micros-floor_usd_micros)
) STRICT;

CREATE TABLE simulated_order (
  id TEXT PRIMARY KEY, window_id TEXT NOT NULL REFERENCES evaluation_window(id), contract_id TEXT NOT NULL REFERENCES contract(id),
  signal_available_at_ns INTEGER NOT NULL, decision_at_ns INTEGER NOT NULL, submitted_at_ns INTEGER NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy','sell')), quantity INTEGER NOT NULL CHECK(quantity>0),
  order_type TEXT NOT NULL CHECK(order_type IN ('market','stop','limit')),
  reference_price_ticks INTEGER NOT NULL, protective_stop_ticks INTEGER,
  risk_budget_usd_micros INTEGER NOT NULL CHECK(risk_budget_usd_micros>=0),
  idempotency_key TEXT NOT NULL, UNIQUE(window_id,idempotency_key),
  CHECK(signal_available_at_ns<=decision_at_ns AND decision_at_ns<=submitted_at_ns)
) STRICT;

CREATE TABLE simulated_fill (
  id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES simulated_order(id), filled_at_ns INTEGER NOT NULL,
  quantity INTEGER NOT NULL CHECK(quantity>0), price_ticks INTEGER NOT NULL,
  commission_usd_micros INTEGER NOT NULL CHECK(commission_usd_micros>=0),
  slippage_ticks REAL NOT NULL, fill_model_evidence_artifact_id TEXT NOT NULL REFERENCES artifact(id)
) STRICT;

CREATE TABLE trade (
  id TEXT PRIMARY KEY, window_id TEXT NOT NULL REFERENCES evaluation_window(id),
  contract_id TEXT NOT NULL REFERENCES contract(id), entry_order_id TEXT NOT NULL REFERENCES simulated_order(id),
  exit_order_id TEXT NOT NULL REFERENCES simulated_order(id),
  direction INTEGER NOT NULL CHECK(direction IN (-1,1)), quantity INTEGER NOT NULL CHECK(quantity>0),
  opened_at_ns INTEGER NOT NULL, closed_at_ns INTEGER NOT NULL,
  gross_pnl_usd_micros INTEGER NOT NULL, fees_usd_micros INTEGER NOT NULL CHECK(fees_usd_micros>=0),
  net_pnl_usd_micros INTEGER NOT NULL, mae_usd_micros INTEGER NOT NULL CHECK(mae_usd_micros>=0),
  mfe_usd_micros INTEGER NOT NULL CHECK(mfe_usd_micros>=0), exit_reason TEXT NOT NULL,
  CHECK(opened_at_ns<closed_at_ns), CHECK(net_pnl_usd_micros=gross_pnl_usd_micros-fees_usd_micros)
) STRICT;

CREATE TABLE metric (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES experiment_run(id),
  window_id TEXT REFERENCES evaluation_window(id), name TEXT NOT NULL,
  value REAL, unit TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('measured','undefined','missing','not_applicable')),
  numerator REAL, denominator REAL,
  definition_artifact_id TEXT NOT NULL REFERENCES artifact(id), scope_json TEXT NOT NULL CHECK(json_valid(scope_json)),
  CHECK((status='measured' AND value IS NOT NULL) OR (status!='measured' AND value IS NULL))
) STRICT;

CREATE TABLE statistical_test (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES experiment_run(id), metric_id TEXT NOT NULL REFERENCES metric(id),
  method TEXT NOT NULL, dependence_unit TEXT NOT NULL, observations INTEGER NOT NULL CHECK(observations>=0),
  effective_observations REAL CHECK(effective_observations>=0),
  family_trials INTEGER NOT NULL CHECK(family_trials>0),
  raw_p_value REAL CHECK(raw_p_value>=0 AND raw_p_value<=1), adjusted_p_value REAL CHECK(adjusted_p_value>=0 AND adjusted_p_value<=1),
  interval_low REAL, interval_high REAL, confidence REAL CHECK(confidence>0 AND confidence<1),
  seed INTEGER NOT NULL, protocol_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  status TEXT NOT NULL CHECK(status IN ('valid','diagnostic','insufficient','invalid')),
  CHECK(interval_low IS NULL OR interval_high IS NULL OR interval_low<=interval_high)
) STRICT;

CREATE TABLE paired_comparison (
  id TEXT PRIMARY KEY, candidate_run_id TEXT NOT NULL REFERENCES experiment_run(id), baseline_run_id TEXT NOT NULL REFERENCES experiment_run(id),
  matching_manifest_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  metric_name TEXT NOT NULL, difference REAL, unit TEXT NOT NULL,
  comparability TEXT NOT NULL CHECK(comparability IN ('matched','confounded','incomparable')),
  explanation TEXT NOT NULL, CHECK(candidate_run_id!=baseline_run_id)
) STRICT;

CREATE TABLE gate_result (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES experiment_run(id), name TEXT NOT NULL,
  policy_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  outcome TEXT NOT NULL CHECK(outcome IN ('pass','fail','blocked','not_applicable')),
  measured_json TEXT NOT NULL CHECK(json_valid(measured_json)), threshold_json TEXT NOT NULL CHECK(json_valid(threshold_json)),
  explanation TEXT NOT NULL, evidence_artifact_id TEXT NOT NULL REFERENCES artifact(id), evaluated_at_ns INTEGER NOT NULL
) STRICT;

CREATE TABLE review (
  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES experiment_run(id), model_attempt_id TEXT REFERENCES model_attempt(id),
  reviewer_kind TEXT NOT NULL CHECK(reviewer_kind IN ('deterministic','model','human')),
  verdict TEXT NOT NULL CHECK(verdict IN ('support','reject','inconclusive','unavailable')),
  content_artifact_id TEXT REFERENCES artifact(id), created_at_ns INTEGER NOT NULL,
  CHECK(reviewer_kind!='model' OR model_attempt_id IS NOT NULL)
) STRICT;

CREATE TABLE research_decision (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id), run_id TEXT NOT NULL REFERENCES experiment_run(id),
  action TEXT NOT NULL CHECK(action IN ('reject','retain','replicate','refine','ablate','request_data','shadow','stop')),
  rationale TEXT NOT NULL, evidence_manifest_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  decided_by TEXT NOT NULL, decided_at_ns INTEGER NOT NULL,
  next_hypothesis_id TEXT REFERENCES hypothesis(id)
) STRICT;

CREATE TABLE iteration (
  id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL REFERENCES campaign(id), number INTEGER NOT NULL CHECK(number>=0),
  parent_iteration_id TEXT REFERENCES iteration(id), decision_id TEXT REFERENCES research_decision(id),
  incumbent_candidate_id TEXT REFERENCES candidate(id), plan_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  registered_at_ns INTEGER NOT NULL, UNIQUE(campaign_id,number)
) STRICT;

CREATE TABLE work_item (
  id TEXT PRIMARY KEY, iteration_id TEXT NOT NULL REFERENCES iteration(id),
  kind TEXT NOT NULL CHECK(kind IN ('ingest','validate','feature','simulate','statistics','model_review','report')),
  dependency_manifest_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  idempotency_key TEXT NOT NULL UNIQUE, priority REAL NOT NULL,
  estimated_seconds REAL CHECK(estimated_seconds>=0), estimated_usd_micros INTEGER CHECK(estimated_usd_micros>=0),
  created_at_ns INTEGER NOT NULL
) STRICT;

CREATE TABLE task_event (
  id TEXT PRIMARY KEY, work_item_id TEXT NOT NULL REFERENCES work_item(id), sequence INTEGER NOT NULL CHECK(sequence>=0),
  event TEXT NOT NULL CHECK(event IN ('queued','leased','started','heartbeat','succeeded','failed','blocked','cancelled','lease_expired')),
  worker_id TEXT, recorded_at_ns INTEGER NOT NULL, lease_until_ns INTEGER,
  payload_json TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(payload_json)), UNIQUE(work_item_id,sequence)
) STRICT;

CREATE TABLE cache_entry (
  key_sha256 TEXT PRIMARY KEY CHECK(length(key_sha256)=64),
  kind TEXT NOT NULL CHECK(kind IN ('data_audit','features','signals','simulation','statistics','model_advice')),
  dependency_manifest_artifact_id TEXT NOT NULL REFERENCES artifact(id),
  result_artifact_id TEXT NOT NULL REFERENCES artifact(id), producer_run_id TEXT REFERENCES experiment_run(id),
  created_at_ns INTEGER NOT NULL, expires_at_ns INTEGER,
  CHECK(expires_at_ns IS NULL OR expires_at_ns>created_at_ns)
) STRICT;

CREATE TABLE failure (
  id TEXT PRIMARY KEY, work_item_id TEXT REFERENCES work_item(id), run_id TEXT REFERENCES experiment_run(id),
  model_attempt_id TEXT REFERENCES model_attempt(id),
  category TEXT NOT NULL CHECK(category IN ('data','causality','accounting','execution','statistical','strategy','resource','provider','operational')),
  fingerprint_sha256 TEXT NOT NULL CHECK(length(fingerprint_sha256)=64),
  sanitized_code TEXT NOT NULL, diagnosis TEXT NOT NULL,
  retryable INTEGER NOT NULL CHECK(retryable IN (0,1)), retry_after_ns INTEGER,
  evidence_artifact_id TEXT REFERENCES artifact(id), observed_at_ns INTEGER NOT NULL,
  CHECK(work_item_id IS NOT NULL OR run_id IS NOT NULL OR model_attempt_id IS NOT NULL)
) STRICT;

CREATE INDEX experiment_queue_lookup ON experiment(campaign_id,purpose,registered_at_ns);
CREATE INDEX run_status_lookup ON experiment_run(status,started_at_ns);
CREATE INDEX window_outcome_lookup ON evaluation_window(run_id,outcome);
CREATE INDEX event_path_lookup ON account_event(window_id,event_at_ns);
CREATE INDEX failure_dedup_lookup ON failure(fingerprint_sha256,observed_at_ns);
CREATE INDEX exposure_lookup ON data_exposure(split_segment_id,exposed_at_ns);
CREATE INDEX model_cost_lookup ON model_attempt(task_id,status,started_at_ns);

-- Bounds remain conservative when settlement usage is unknown.
CREATE TRIGGER budget_limit BEFORE INSERT ON budget_reservation
WHEN NEW.reserved_amount + COALESCE((
  SELECT SUM(CASE WHEN s.status='measured' THEN s.actual_amount
                  WHEN s.status='released' THEN 0 ELSE r.reserved_amount END)
  FROM budget_reservation r LEFT JOIN budget_settlement s ON s.reservation_id=r.id
  WHERE r.budget_id=NEW.budget_id
),0) > (SELECT limit_amount FROM resource_budget WHERE id=NEW.budget_id)
BEGIN SELECT RAISE(ABORT,'resource budget exhausted'); END;

CREATE TRIGGER enforce_trial_limit BEFORE INSERT ON experiment
WHEN NEW.trial_number>(SELECT max_trials FROM campaign WHERE id=NEW.campaign_id)
BEGIN SELECT RAISE(ABORT,'registered trial limit exceeded'); END;

CREATE TRIGGER fill_causality BEFORE INSERT ON simulated_fill
WHEN NEW.filled_at_ns<=(SELECT decision_at_ns FROM simulated_order WHERE id=NEW.order_id)
BEGIN SELECT RAISE(ABORT,'fill must occur after decision'); END;

CREATE TRIGGER gate_before_shadow BEFORE INSERT ON research_decision
WHEN NEW.action='shadow' AND (
  (SELECT COUNT(DISTINCT name) FROM gate_result WHERE run_id=NEW.run_id AND outcome='pass'
   AND name IN ('data_provenance','causality','execution','out_of_sample','statistics','economics','manual_policy')) != 7 OR
  EXISTS(SELECT 1 FROM gate_result WHERE run_id=NEW.run_id AND outcome IN ('fail','blocked')) OR
  NOT EXISTS(SELECT 1 FROM experiment_run r JOIN experiment e ON e.id=r.experiment_id
             JOIN split_plan s ON s.id=e.split_plan_id JOIN dataset_snapshot d ON d.id=s.dataset_id
             WHERE r.id=NEW.run_id AND r.status='completed' AND d.promotion_eligible=1)
)
BEGIN SELECT RAISE(ABORT,'shadow requires recorded passing gates'); END;

CREATE TRIGGER experiment_campaign_integrity BEFORE INSERT ON experiment
WHEN EXISTS(SELECT 1 FROM candidate WHERE id=NEW.candidate_id AND campaign_id!=NEW.campaign_id)
 OR EXISTS(SELECT 1 FROM split_plan WHERE id=NEW.split_plan_id AND campaign_id!=NEW.campaign_id)
 OR EXISTS(SELECT 1 FROM rule_snapshot WHERE id=NEW.rule_snapshot_id AND campaign_id!=NEW.campaign_id)
 OR EXISTS(SELECT 1 FROM cost_scenario WHERE id=NEW.cost_scenario_id AND campaign_id!=NEW.campaign_id)
 OR EXISTS(SELECT 1 FROM execution_model WHERE id=NEW.execution_model_id AND campaign_id!=NEW.campaign_id)
BEGIN SELECT RAISE(ABORT,'experiment dependencies belong to another campaign'); END;

-- Research definitions are immutable; changes create new identities.
CREATE TRIGGER campaign_no_update BEFORE UPDATE ON campaign BEGIN SELECT RAISE(ABORT,'campaign immutable'); END;
CREATE TRIGGER rules_no_update BEFORE UPDATE ON rule_snapshot BEGIN SELECT RAISE(ABORT,'rules immutable'); END;
CREATE TRIGGER dataset_no_update BEFORE UPDATE ON dataset_snapshot BEGIN SELECT RAISE(ABORT,'dataset immutable'); END;
CREATE TRIGGER candidate_no_update BEFORE UPDATE ON candidate BEGIN SELECT RAISE(ABORT,'candidate immutable'); END;
CREATE TRIGGER experiment_no_update BEFORE UPDATE ON experiment BEGIN SELECT RAISE(ABORT,'experiment immutable'); END;
CREATE TRIGGER decision_no_update BEFORE UPDATE ON research_decision BEGIN SELECT RAISE(ABORT,'decision immutable'); END;

CREATE VIEW budget_usage AS
SELECT b.id,b.category,b.limit_amount,
 COALESCE(SUM(CASE WHEN s.status='measured' THEN s.actual_amount WHEN s.status='released' THEN 0 ELSE r.reserved_amount END),0) AS charged_amount
FROM resource_budget b LEFT JOIN budget_reservation r ON r.budget_id=b.id
LEFT JOIN budget_settlement s ON s.reservation_id=r.id GROUP BY b.id;

CREATE VIEW latest_task_state AS
SELECT e.* FROM task_event e WHERE e.sequence=(SELECT MAX(x.sequence) FROM task_event x WHERE x.work_item_id=e.work_item_id);

CREATE TRIGGER immutable_artifact BEFORE UPDATE ON artifact BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_artifact BEFORE DELETE ON artifact BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER retain_campaign BEFORE DELETE ON campaign BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_source_document BEFORE UPDATE ON source_document BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_source_document BEFORE DELETE ON source_document BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER retain_rule_snapshot BEFORE DELETE ON rule_snapshot BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_instrument BEFORE UPDATE ON instrument BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_instrument BEFORE DELETE ON instrument BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_contract BEFORE UPDATE ON contract BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_contract BEFORE DELETE ON contract BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_calendar_snapshot BEFORE UPDATE ON calendar_snapshot BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_calendar_snapshot BEFORE DELETE ON calendar_snapshot BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_session BEFORE UPDATE ON session BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_session BEFORE DELETE ON session BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER retain_dataset_snapshot BEFORE DELETE ON dataset_snapshot BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_dataset_partition BEFORE UPDATE ON dataset_partition BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_dataset_partition BEFORE DELETE ON dataset_partition BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_roll_assignment BEFORE UPDATE ON roll_assignment BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_roll_assignment BEFORE DELETE ON roll_assignment BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_data_quality_check BEFORE UPDATE ON data_quality_check BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_data_quality_check BEFORE DELETE ON data_quality_check BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_split_plan BEFORE UPDATE ON split_plan BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_split_plan BEFORE DELETE ON split_plan BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_split_segment BEFORE UPDATE ON split_segment BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_split_segment BEFORE DELETE ON split_segment BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_data_exposure BEFORE UPDATE ON data_exposure BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_data_exposure BEFORE DELETE ON data_exposure BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_feature_definition BEFORE UPDATE ON feature_definition BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_feature_definition BEFORE DELETE ON feature_definition BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_feature_partition BEFORE UPDATE ON feature_partition BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_feature_partition BEFORE DELETE ON feature_partition BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_hypothesis BEFORE UPDATE ON hypothesis BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_hypothesis BEFORE DELETE ON hypothesis BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER retain_candidate BEFORE DELETE ON candidate BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_candidate_change BEFORE UPDATE ON candidate_change BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_candidate_change BEFORE DELETE ON candidate_change BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_cost_scenario BEFORE UPDATE ON cost_scenario BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_cost_scenario BEFORE DELETE ON cost_scenario BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_execution_model BEFORE UPDATE ON execution_model BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_execution_model BEFORE DELETE ON execution_model BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_resource_budget BEFORE UPDATE ON resource_budget BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_resource_budget BEFORE DELETE ON resource_budget BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_budget_reservation BEFORE UPDATE ON budget_reservation BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_budget_reservation BEFORE DELETE ON budget_reservation BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_budget_settlement BEFORE UPDATE ON budget_settlement BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_budget_settlement BEFORE DELETE ON budget_settlement BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_model_task BEFORE UPDATE ON model_task BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_model_task BEFORE DELETE ON model_task BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_model_attempt BEFORE UPDATE ON model_attempt BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_model_attempt BEFORE DELETE ON model_attempt BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER retain_experiment BEFORE DELETE ON experiment BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_evaluation_window BEFORE UPDATE ON evaluation_window BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_evaluation_window BEFORE DELETE ON evaluation_window BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_account_event BEFORE UPDATE ON account_event BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_account_event BEFORE DELETE ON account_event BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_simulated_order BEFORE UPDATE ON simulated_order BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_simulated_order BEFORE DELETE ON simulated_order BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_simulated_fill BEFORE UPDATE ON simulated_fill BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_simulated_fill BEFORE DELETE ON simulated_fill BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_trade BEFORE UPDATE ON trade BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_trade BEFORE DELETE ON trade BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_metric BEFORE UPDATE ON metric BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_metric BEFORE DELETE ON metric BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_statistical_test BEFORE UPDATE ON statistical_test BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_statistical_test BEFORE DELETE ON statistical_test BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_paired_comparison BEFORE UPDATE ON paired_comparison BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_paired_comparison BEFORE DELETE ON paired_comparison BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_gate_result BEFORE UPDATE ON gate_result BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_gate_result BEFORE DELETE ON gate_result BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_review BEFORE UPDATE ON review BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_review BEFORE DELETE ON review BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER retain_research_decision BEFORE DELETE ON research_decision BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_iteration BEFORE UPDATE ON iteration BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_iteration BEFORE DELETE ON iteration BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_work_item BEFORE UPDATE ON work_item BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_work_item BEFORE DELETE ON work_item BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_task_event BEFORE UPDATE ON task_event BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_task_event BEFORE DELETE ON task_event BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_cache_entry BEFORE UPDATE ON cache_entry BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_cache_entry BEFORE DELETE ON cache_entry BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER immutable_failure BEFORE UPDATE ON failure BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER retain_failure BEFORE DELETE ON failure BEGIN SELECT RAISE(ABORT,'append-only research store'); END;
CREATE TRIGGER retain_run BEFORE DELETE ON experiment_run BEGIN SELECT RAISE(ABORT,'retain run evidence'); END;
CREATE TRIGGER sealed_run BEFORE UPDATE ON experiment_run WHEN OLD.status IN ('completed','failed','cancelled','interrupted','blocked') BEGIN SELECT RAISE(ABORT,'terminal run immutable'); END;

COMMIT;
