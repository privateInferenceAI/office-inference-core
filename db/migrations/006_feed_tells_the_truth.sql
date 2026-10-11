-- 006_feed_tells_the_truth.sql
--
-- audit_feed rendered every model call as "answered", including the ones the gateway refused before
-- spending anything. A view built for a future Activity screen should not tell a reader that a model
-- answered when it did not, so the summary now follows the outcome.

create or replace view audit_feed as
select id, occurred_at, event_type, actor, actor_role, data_class, outcome,
       tool, model, request_id,
       case
         when outcome in ('denied', 'error') and event_type = 'model_call'
           then coalesce(model, 'a model') || case when outcome = 'denied'
                                                   then ' was refused'
                                                   else ' failed' end
         when outcome in ('denied', 'error') and event_type = 'tool_call'
           then coalesce(tool, 'a tool') || case when outcome = 'denied'
                                                 then ' was refused'
                                                 else ' failed' end
         when event_type = 'model_call' then coalesce(model, 'a model') || ' answered'
         when event_type = 'tool_call'  then coalesce(tool, 'a tool') || ' was used'
         when event_type = 'approval'   then 'a person ' || outcome || ' an action'
         when event_type = 'access'     then 'a document was read'
         when event_type = 'purge'      then 'a retention purge removed '
                                             || coalesce(payload ->> 'deleted_count', '?') || ' event(s)'
         else event_type
       end as summary,
       payload_sha256, row_hash
from audit_events
order by id desc;

comment on view audit_feed is
  'Plain-language lines for a reader. The summary follows the outcome, so a refused call reads as
   refused rather than as one that answered.';
