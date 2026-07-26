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
DEFINE FIELD IF NOT EXISTS created_at ON car TYPE datetime DEFAULT time::now();

-- Трата
DEFINE TABLE IF NOT EXISTS expense SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS chat_id ON expense TYPE int;
DEFINE FIELD IF NOT EXISTS author ON expense TYPE record<person>;
DEFINE FIELD IF NOT EXISTS author_id ON expense TYPE int;
DEFINE FIELD IF NOT EXISTS author_name ON expense TYPE string;
DEFINE FIELD IF NOT EXISTS message_id ON expense TYPE int;
DEFINE FIELD IF NOT EXISTS amount ON expense TYPE decimal;
DEFINE FIELD IF NOT EXISTS category ON expense TYPE string
    ASSERT $value IN ['fuel', 'service', 'other'];
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
