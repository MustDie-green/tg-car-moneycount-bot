-- Схема SurrealDB. Применяется при каждом старте бота, идемпотентна.

-- Участник чата. id составной: person:[chat_id, tg_id]
DEFINE TABLE IF NOT EXISTS person SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON person TYPE int;
DEFINE FIELD IF NOT EXISTS tg_id ON person TYPE int;
DEFINE FIELD IF NOT EXISTS name ON person TYPE string;
DEFINE FIELD IF NOT EXISTS username ON person TYPE option<string>;
DEFINE FIELD IF NOT EXISTS created_at ON person TYPE datetime DEFAULT time::now();

-- Машина. id составной: car:[chat_id, tg_id, slug]
DEFINE TABLE IF NOT EXISTS car SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON car TYPE int;
DEFINE FIELD IF NOT EXISTS owner_id ON car TYPE int;
DEFINE FIELD IF NOT EXISTS title ON car TYPE string;
DEFINE FIELD IF NOT EXISTS active ON car TYPE bool DEFAULT true;
-- Со слов владельца: нужен, пока замеров одометра мало для своей оценки
DEFINE FIELD IF NOT EXISTS monthly_km ON car TYPE option<int>;
DEFINE FIELD IF NOT EXISTS created_at ON car TYPE datetime DEFAULT time::now();

-- Замер одометра сам по себе: «пробег 123456» без траты
DEFINE TABLE IF NOT EXISTS odometer SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON odometer TYPE int;
DEFINE FIELD IF NOT EXISTS owner_id ON odometer TYPE int;
DEFINE FIELD IF NOT EXISTS car ON odometer TYPE option<record<car>>;
DEFINE FIELD IF NOT EXISTS km ON odometer TYPE int;
DEFINE FIELD IF NOT EXISTS at ON odometer TYPE datetime;
DEFINE INDEX IF NOT EXISTS odometer_owner ON odometer FIELDS chat_id, owner_id, at;

-- Позиция обслуживания. id составной: service:[chat_id, owner_id, slug, kind]
-- Следующая замена либо задана явно, либо считается как последняя + интервал.
-- Срабатывает то, что ближе: километры или дни.
DEFINE TABLE IF NOT EXISTS service SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON service TYPE int;
DEFINE FIELD IF NOT EXISTS owner_id ON service TYPE int;
DEFINE FIELD IF NOT EXISTS car ON service TYPE record<car>;
DEFINE FIELD IF NOT EXISTS car_slug ON service TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS car_title ON service TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS kind ON service TYPE string;
DEFINE FIELD IF NOT EXISTS interval_km ON service TYPE option<int>;
DEFINE FIELD IF NOT EXISTS interval_days ON service TYPE option<int>;
DEFINE FIELD IF NOT EXISTS last_km ON service TYPE option<int>;
DEFINE FIELD IF NOT EXISTS last_at ON service TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS next_km ON service TYPE option<int>;
DEFINE FIELD IF NOT EXISTS next_at ON service TYPE option<datetime>;
-- Когда последний раз напомнили: чтобы не долбить каждый день
DEFINE FIELD IF NOT EXISTS reminded_at ON service TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS updated_at ON service TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS service_owner ON service FIELDS chat_id, owner_id;

-- Личка: какой чат обслуживаем, когда человек пишет боту напрямую
DEFINE TABLE IF NOT EXISTS dm SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS tg_id ON dm TYPE int;
DEFINE FIELD IF NOT EXISTS chat_id ON dm TYPE int;
DEFINE FIELD IF NOT EXISTS updated_at ON dm TYPE datetime DEFAULT time::now();

-- Трата
DEFINE TABLE IF NOT EXISTS expense SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON expense TYPE int;
DEFINE FIELD IF NOT EXISTS author ON expense TYPE record<person>;
DEFINE FIELD IF NOT EXISTS author_id ON expense TYPE int;
DEFINE FIELD IF NOT EXISTS author_name ON expense TYPE string;
DEFINE FIELD IF NOT EXISTS message_id ON expense TYPE int;
DEFINE FIELD IF NOT EXISTS amount ON expense TYPE decimal;
-- OVERWRITE, а не IF NOT EXISTS: список категорий менялся, и старое
-- ограничение в уже созданной базе иначе так и осталось бы прежним.
-- 'other' оставлен, пока миграция не разнесла старые записи по новым.
DEFINE FIELD OVERWRITE category ON expense TYPE string
    ASSERT $value IN ['fuel', 'service', 'fines', 'care', 'state', 'other'];
DEFINE FIELD IF NOT EXISTS note ON expense TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS liters ON expense TYPE option<decimal>;
DEFINE FIELD IF NOT EXISTS odometer ON expense TYPE option<int>;
DEFINE FIELD IF NOT EXISTS car ON expense TYPE option<record<car>>;
DEFINE FIELD IF NOT EXISTS spent_at ON expense TYPE datetime;
DEFINE FIELD IF NOT EXISTS created_at ON expense TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS expense_period ON expense FIELDS chat_id, spent_at;
DEFINE INDEX IF NOT EXISTS expense_message ON expense FIELDS chat_id, message_id;

-- Самообучаемый словарь чата. id составной: lexicon:[chat_id, word]
DEFINE TABLE IF NOT EXISTS lexicon SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON lexicon TYPE int;
DEFINE FIELD IF NOT EXISTS word ON lexicon TYPE string;
DEFINE FIELD IF NOT EXISTS category ON lexicon TYPE option<string>;
DEFINE FIELD IF NOT EXISTS votes_yes ON lexicon TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS votes_no ON lexicon TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS updated_at ON lexicon TYPE datetime DEFAULT time::now();

-- Вопрос «На тачку?», ждущий ответа. id составной: pending:[chat_id, message_id]
DEFINE TABLE IF NOT EXISTS pending SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON pending TYPE int;
DEFINE FIELD IF NOT EXISTS message_id ON pending TYPE int;
DEFINE FIELD IF NOT EXISTS question_id ON pending TYPE int;
DEFINE FIELD IF NOT EXISTS author_id ON pending TYPE int;
DEFINE FIELD IF NOT EXISTS author_name ON pending TYPE string;
DEFINE FIELD IF NOT EXISTS username ON pending TYPE option<string>;
-- detections + unknown_lemmas, сериализованные в JSON
DEFINE FIELD IF NOT EXISTS payload ON pending TYPE string;
DEFINE FIELD IF NOT EXISTS spent_at ON pending TYPE datetime;
DEFINE FIELD IF NOT EXISTS expires_at ON pending TYPE datetime;

-- Чаты, где бот уже что-то записал: по ним рассылается ежемесячный отчёт
DEFINE TABLE IF NOT EXISTS chat SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON chat TYPE int;
DEFINE FIELD IF NOT EXISTS title ON chat TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS last_seen ON chat TYPE datetime DEFAULT time::now();

-- Журнал наблюдений детектора: каждое сообщение, где встретилась похожая на
-- деньги сумма, вместе с вердиктом и (если спрашивали) ответом человека.
-- Нужен, чтобы через пару недель посмотреть, где детектор врёт, на данных,
-- а не на догадках.
DEFINE TABLE IF NOT EXISTS detect_log SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON detect_log TYPE int;
DEFINE FIELD IF NOT EXISTS message_id ON detect_log TYPE int;
DEFINE FIELD IF NOT EXISTS author_id ON detect_log TYPE int;
DEFINE FIELD IF NOT EXISTS author_name ON detect_log TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS text ON detect_log TYPE string DEFAULT '';
-- keyword:мойка / explicit / no_keyword / veto:стоить
DEFINE FIELD IF NOT EXISTS reason ON detect_log TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS amounts ON detect_log TYPE string DEFAULT '';
DEFINE FIELD IF NOT EXISTS asked ON detect_log TYPE bool DEFAULT false;
-- yes / no / timeout — чем кончился заданный вопрос
DEFINE FIELD IF NOT EXISTS answer ON detect_log TYPE option<string>;
DEFINE FIELD IF NOT EXISTS at ON detect_log TYPE datetime DEFAULT time::now();
DEFINE INDEX IF NOT EXISTS detect_log_chat ON detect_log FIELDS chat_id, at;

-- Открытый вопрос «Отметить в ТО?». id составной: service_ask:[msg_chat, message_id]
-- msg_chat — где висит сообщение с кнопками (общий чат или личка),
-- group_id — чат, к которому относятся машина и её обслуживание.
DEFINE TABLE IF NOT EXISTS service_ask SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS msg_chat ON service_ask TYPE int;
DEFINE FIELD IF NOT EXISTS message_id ON service_ask TYPE int;
DEFINE FIELD IF NOT EXISTS group_id ON service_ask TYPE int;
DEFINE FIELD IF NOT EXISTS owner_id ON service_ask TYPE int;
DEFINE FIELD IF NOT EXISTS car ON service_ask TYPE record<car>;
DEFINE FIELD IF NOT EXISTS car_slug ON service_ask TYPE string;
DEFINE FIELD IF NOT EXISTS car_title ON service_ask TYPE string;
DEFINE FIELD IF NOT EXISTS offered ON service_ask TYPE array<string>;
DEFINE FIELD IF NOT EXISTS selected ON service_ask TYPE array<string>;
DEFINE FIELD IF NOT EXISTS km ON service_ask TYPE option<int>;
-- km прикинут по темпу езды, а не измерен
DEFINE FIELD IF NOT EXISTS km_estimated ON service_ask TYPE bool DEFAULT false;
-- ждём, что человек ответит на сообщение своим пробегом
DEFINE FIELD IF NOT EXISTS awaiting_km ON service_ask TYPE bool DEFAULT false;
DEFINE FIELD IF NOT EXISTS at ON service_ask TYPE datetime;
DEFINE FIELD IF NOT EXISTS expires_at ON service_ask TYPE datetime;
