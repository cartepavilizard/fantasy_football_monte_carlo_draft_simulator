#!/usr/bin/env Rscript
# FFANALYTICS PRODUCER SCRIPT
#
# Scrapes ffanalytics's ~12-source projection roster, scores it to this
# league's scoring format (not generic PPR — the one thing no other
# current source does), and writes a CSV in the schema H1's push-source
# path already understands: name,position,nfl_team,rank,position_rank,
# tier,projection (see backend/data_sources/udk.py's COLUMN_ALIASES).
# That means a future row can register this as a push source
# (register_push_source("ffanalytics", parse_udk_rows)) and reuse udk's
# parser unchanged.
#
# Usage:
#   Rscript ffanalytics_export.R [--season=2026] [--week=0]
#     [--scoring=ppr] [--out=ffanalytics_export.csv]
#     [--pos=QB,RB,WR,TE,K,DST] [--src=CBS,ESPN,...]
#
# --scoring mirrors backend/models/config.py's SCORING_FORMAT
# (standard|half_ppr|ppr). It only controls reception scoring (0 / 0.5 / 1)
# because that is the only scoring dimension the backend actually
# configures today; every other stat uses ffanalytics's built-in defaults.
#
# custom_scoring() only populates $pts_bracket when DST bonus-bracket args
# are passed, but projections_table() unconditionally rapply()s over it and
# crashes if it's missing (found in H3's spike). Workaround verified there
# and required here: copy the package default bracket onto the custom
# scoring object before calling projections_table().
suppressPackageStartupMessages(library(ffanalytics))

parse_args <- function(argv) {
  defaults <- list(
    season = format(Sys.Date(), "%Y"),
    week = "0",
    scoring = "ppr",
    out = "ffanalytics_export.csv",
    pos = "QB,RB,WR,TE,K,DST",
    src = paste(
      c(
        "CBS", "ESPN", "FantasyPros", "FantasySharks", "FFToday",
        "FleaFlicker", "NumberFire", "FantasyFootballNerd", "NFL",
        "RTSports", "Walterfootball", "FanDuel"
      ),
      collapse = ","
    )
  )
  for (arg in argv) {
    m <- regmatches(arg, regexec("^--([a-z]+)=(.*)$", arg))[[1]]
    if (length(m) == 3) {
      defaults[[m[2]]] <- m[3]
    }
  }
  defaults
}

reception_points <- function(scoring_format) {
  switch(scoring_format,
    standard = 0,
    half_ppr = 0.5,
    ppr = 1,
    stop("ffanalytics_export: unsupported --scoring '", scoring_format, "'")
  )
}

build_scoring_rules <- function(scoring_format) {
  my_scoring <- custom_scoring(
    pass_yds = 0.04, pass_tds = 4, pass_int = -2,
    rush_yds = 0.1, rush_tds = 6,
    rec = reception_points(scoring_format), rec_yds = 0.1, rec_tds = 6,
    fumbles_lost = -2, two_pts = 2,
    xp = 1, fg_0019 = 3, fg_2029 = 3, fg_3039 = 3, fg_4049 = 4, fg_50 = 5,
    dst_fum_rec = 2, dst_int = 2, dst_safety = 2, dst_sacks = 1,
    dst_td = 6, dst_blk = 1.5
  )
  # H3 workaround: projections_table() crashes without pts_bracket.
  my_scoring$pts_bracket <- scoring$pts_bracket
  my_scoring
}

main <- function() {
  args <- parse_args(commandArgs(trailingOnly = TRUE))
  season <- as.integer(args$season)
  week <- as.integer(args$week)
  pos <- strsplit(args$pos, ",")[[1]]
  src <- strsplit(args$src, ",")[[1]]

  cat(sprintf(
    "Scraping season=%d week=%d scoring=%s pos=%s src=%s\n",
    season, week, args$scoring, paste(pos, collapse = "/"), paste(src, collapse = "/")
  ))
  raw <- scrape_data(src = src, pos = pos, season = season, week = week)

  scoring_rules <- build_scoring_rules(args$scoring)
  scored <- projections_table(raw, scoring_rules = scoring_rules)
  players <- add_player_info(scored)

  total_rows <- nrow(players)
  unmatched <- is.na(players$first_name) | is.na(players$last_name)
  if (any(unmatched)) {
    cat(sprintf(
      "Dropping %d/%d rows with no player-name match (add_player_info couldn't resolve them)\n",
      sum(unmatched), total_rows
    ))
    players <- players[!unmatched, ]
  }

  na_points <- sum(is.na(players$points))
  zero_points <- sum(!is.na(players$points) & players$points == 0)
  if (na_points > 0 || zero_points > 0) {
    cat(sprintf(
      "NOTE: %d rows with NA points, %d rows with a zero-point projection — verify these aren't sentinel values before trusting the export\n",
      na_points, zero_points
    ))
  }

  # Header names match backend/data_sources/udk.py's COLUMN_ALIASES exactly
  # (not just the field names) so parse_udk_rows() recognizes every column.
  out <- data.frame(
    name = trimws(paste(players$first_name, players$last_name)),
    position = players$position,
    team = players$team,
    rank = players$rank,
    "position rank" = players$pos_rank,
    tier = players$tier,
    projection = round(players$points, 2),
    check.names = FALSE
  )

  write.csv(out, args$out, row.names = FALSE, na = "")
  cat(sprintf("Wrote %d rows to %s\n", nrow(out), args$out))
}

main()
