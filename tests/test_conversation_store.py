import os, tempfile, pytest
from castlearq_desktop.storage.conversation_store import ConversationStore, ConversationStoreError

def test_save_load():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        msgs=[{'role':'user','content':'hi'},{'role':'assistant','content':'hello'}]
        s.save_conversation('c1',msgs)
        assert s.load_conversation('c1')==msgs

def test_separate():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('a',[{'role':'user','content':'A'}])
        s.save_conversation('b',[{'role':'user','content':'B'}])
        assert s.load_conversation('a')==[{'role':'user','content':'A'}]
        assert s.load_conversation('b')==[{'role':'user','content':'B'}]

def test_persistence_after_reopen():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        ConversationStore(p).save_conversation('c',[{'role':'user','content':'x'}])
        assert ConversationStore(p).load_conversation('c')==[{'role':'user','content':'x'}]

def test_no_db_before_save():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        assert not os.path.exists(p)
        assert s.load_conversation('c')==[]
        assert not os.path.exists(p)

def test_repeat_no_duplicates():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        msgs=[{'role':'user','content':'hi'}]
        s.save_conversation('c',msgs)
        s.save_conversation('c',msgs)
        assert s.load_conversation('c')==msgs

def test_update_replace():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('c',[{'role':'user','content':'old'}])
        s.save_conversation('c',[{'role':'user','content':'new'}])
        assert s.load_conversation('c')==[{'role':'user','content':'new'}]

def test_invalid_inputs():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        with pytest.raises(ValueError):
            s.save_conversation('',[{'role':'user','content':'x'}])
        with pytest.raises(ValueError):
            s.save_conversation('c',[{'role':'','content':'x'}])
        with pytest.raises(ValueError):
            s.save_conversation('c','bad')

def test_atomicity():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('c',[{'role':'user','content':'orig'}])
        try:
            s.save_conversation('c',[{'role':'user','content':'x'},{'role':'','content':'bad'}])
        except ValueError:
            pass
        assert s.load_conversation('c')==[{'role':'user','content':'orig'}]

def test_inaccessible_db():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'dir','t.db')
        os.makedirs(os.path.join(d,'dir'))
        # make directory unreadable? skip; instead invalid path parent file
        bad=os.path.join(d,'file')
        with open(bad,'w') as f: f.write('x')
        s=ConversationStore(os.path.join(bad,'t.db'))
        with pytest.raises(ConversationStoreError):
            s.save_conversation('c',[{'role':'user','content':'x'}])

def test_store_error_load():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        # no db -> empty
        assert s.load_conversation('c')==[]

def test_atomicity_real_sqlite_failure():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        initial=[{'role':'user','content':'orig1'},{'role':'assistant','content':'orig2'}]
        s.save_conversation('c',initial)
        conn=sqlite3.connect(p)
        conn.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON messages BEGIN SELECT RAISE(ABORT,'forced failure'); END;")
        conn.commit()
        conn.close()
        try:
            s.save_conversation('c',[{'role':'user','content':'new1'},{'role':'assistant','content':'new2'}])
        except ConversationStoreError:
            pass
        else:
            raise AssertionError('expected ConversationStoreError')
        assert s.load_conversation('c')==initial

def test_delete_existing():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('c',[{'role':'user','content':'hi'}])
        assert s.delete_conversation('c') is True
        assert s.load_conversation('c')==[]

def test_delete_cascade():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('c',[{'role':'user','content':'hi'}])
        assert s.delete_conversation('c') is True
        # messages gone: list shows no conversation
        assert s.list_conversations()==[]

def test_delete_isolation():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('a',[{'role':'user','content':'A'}])
        s.save_conversation('b',[{'role':'user','content':'B'}])
        assert s.delete_conversation('a') is True
        assert s.load_conversation('a')==[]
        assert s.load_conversation('b')==[{'role':'user','content':'B'}]

def test_delete_missing():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('a',[{'role':'user','content':'A'}])
        assert s.delete_conversation('nope') is False
        assert s.load_conversation('a')==[{'role':'user','content':'A'}]

def test_delete_sqlite_error():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('c',[{'role':'user','content':'orig'}])
        conn=sqlite3.connect(p)
        conn.execute("CREATE TRIGGER fail_del BEFORE DELETE ON conversations BEGIN SELECT RAISE(ABORT,'forced'); END;")
        conn.commit(); conn.close()
        with pytest.raises(ConversationStoreError):
            s.delete_conversation('c')

def test_delete_atomicity():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('a',[{'role':'user','content':'A'}])
        s.save_conversation('b',[{'role':'user','content':'B'}])
        conn=sqlite3.connect(p)
        conn.execute("CREATE TRIGGER fail_del BEFORE DELETE ON conversations BEGIN SELECT RAISE(ABORT,'forced'); END;")
        conn.commit(); conn.close()
        try:
            s.delete_conversation('a')
        except ConversationStoreError:
            pass
        # both remain
        assert s.load_conversation('a')==[{'role':'user','content':'A'}]
        assert s.load_conversation('b')==[{'role':'user','content':'B'}]

def test_delete_no_db():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        assert s.delete_conversation('c') is False
        assert not os.path.exists(p)
def test_rename_existing():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('c',[{'role':'user','content':'hi'}],model_id='m',artifact_filename='a')
        assert s.rename_conversation('c','  My Title  ') is True
        convs=s.list_conversations()
        assert convs[0]['title']=='My Title'
        assert convs[0]['id']=='c'

def test_rename_missing():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('a',[{'role':'user','content':'A'}])
        assert s.rename_conversation('nope','x') is False

def test_rename_empty():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('c',[{'role':'user','content':'hi'}])
        with pytest.raises(ValueError):
            s.rename_conversation('c','')
        with pytest.raises(ValueError):
            s.rename_conversation('c','   ')

def test_rename_duplicates_allowed():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('a',[{'role':'user','content':'A'}])
        s.save_conversation('b',[{'role':'user','content':'B'}])
        assert s.rename_conversation('a','Same') is True
        assert s.rename_conversation('b','Same') is True

def test_rename_preserves_data():
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        msgs=[{'role':'user','content':'hi'},{'role':'assistant','content':'hello'}]
        s.save_conversation('c',msgs,model_id='m',artifact_filename='a')
        before=s.list_conversations()[0]
        s.rename_conversation('c','New')
        after=s.list_conversations()[0]
        assert after['id']==before['id']
        assert after['model_id']==before['model_id']
        assert after['artifact_filename']==before['artifact_filename']
        assert after['created_at']==before['created_at']
        assert after['updated_at']==before['updated_at']
        assert s.load_conversation('c')==msgs

def test_rename_sqlite_error():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        s=ConversationStore(p)
        s.save_conversation('c',[{'role':'user','content':'orig'}])
        conn=sqlite3.connect(p)
        conn.execute("CREATE TRIGGER fail_rename BEFORE UPDATE ON conversations BEGIN SELECT RAISE(ABORT,'forced'); END;")
        conn.commit(); conn.close()
        with pytest.raises(ConversationStoreError):
            s.rename_conversation('c','x')

def test_migration_old_schema():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        p=os.path.join(d,'t.db')
        conn=sqlite3.connect(p)
        conn.executescript("""
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            model_id TEXT,
            artifact_filename TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL,
            turn_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(conversation_id, turn_index),
            FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
        );
        """)
        conn.execute("INSERT INTO conversations (id,model_id,artifact_filename) VALUES ('c','m','a')")
        conn.execute("INSERT INTO messages (conversation_id,turn_index,role,content) VALUES ('c',0,'user','hi')")
        conn.commit(); conn.close()
        s=ConversationStore(p)
        # trigger init via list
        convs=s.list_conversations()
        assert convs[0]['title'] is None
        cols={r[1] for r in sqlite3.connect(p).execute("PRAGMA table_info(conversations)").fetchall()}
        assert 'title' in cols
        assert s.load_conversation('c')==[{'role':'user','content':'hi'}]
        # repeat init
        s2=ConversationStore(p)
        assert s2.list_conversations()[0]['title'] is None
