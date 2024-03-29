import datetime
import unittest

from django.test import TransactionTestCase
from django.db import connection, DatabaseError, IntegrityError, OperationalError
from django.db.models.fields import (BinaryField, BooleanField, CharField, IntegerField,
    PositiveIntegerField, SlugField, TextField)
from django.db.models.fields.related import ManyToManyField, ForeignKey
from django.db.transaction import atomic
from .models import (Author, AuthorWithDefaultHeight, AuthorWithM2M, Book, BookWithLongName,
    BookWithSlug, BookWithM2M, Tag, TagIndexed, TagM2MTest, TagUniqueRename,
    UniqueTest, Thing, TagThrough, BookWithM2MThrough, AuthorTag, AuthorWithM2MThrough,
    AuthorWithEvenLongerName, BookWeak)


class SchemaTests(TransactionTestCase):
    """
    Tests that the schema-alteration code works correctly.

    Be aware that these tests are more liable than most to false results,
    as sometimes the code to check if a test has worked is almost as complex
    as the code it is testing.
    """

    available_apps = []

    models = [
        Author, AuthorWithM2M, Book, BookWithLongName, BookWithSlug,
        BookWithM2M, Tag, TagIndexed, TagM2MTest, TagUniqueRename, UniqueTest,
        Thing, TagThrough, BookWithM2MThrough, AuthorWithEvenLongerName,
        BookWeak,
    ]

    # Utility functions

    def tearDown(self):
        # Delete any tables made for our models
        self.delete_tables()

    def delete_tables(self):
        "Deletes all model tables for our models for a clean test environment"
        with connection.cursor() as cursor:
            connection.disable_constraint_checking()
            table_names = connection.introspection.table_names(cursor)
            for model in self.models:
                # Remove any M2M tables first
                for field in model._meta.local_many_to_many:
                    with atomic():
                        tbl = field.rel.through._meta.db_table
                        if tbl in table_names:
                            cursor.execute(connection.schema_editor().sql_delete_table % {
                                "table": connection.ops.quote_name(tbl),
                            })
                            table_names.remove(tbl)
                # Then remove the main tables
                with atomic():
                    tbl = model._meta.db_table
                    if tbl in table_names:
                        cursor.execute(connection.schema_editor().sql_delete_table % {
                            "table": connection.ops.quote_name(tbl),
                        })
                        table_names.remove(tbl)
        connection.enable_constraint_checking()

    def column_classes(self, model):
        with connection.cursor() as cursor:
            columns = dict(
                (d[0], (connection.introspection.get_field_type(d[1], d), d))
                for d in connection.introspection.get_table_description(
                    cursor,
                    model._meta.db_table,
                )
            )
        # SQLite has a different format for field_type
        for name, (type, desc) in columns.items():
            if isinstance(type, tuple):
                columns[name] = (type[0], desc)
        # SQLite also doesn't error properly
        if not columns:
            raise DatabaseError("Table does not exist (empty pragma)")
        return columns

    def get_indexes(self, table):
        """
        Get the indexes on the table using a new cursor.
        """
        with connection.cursor() as cursor:
            return connection.introspection.get_indexes(cursor, table)

    def get_constraints(self, table):
        """
        Get the constraints on a table using a new cursor.
        """
        with connection.cursor() as cursor:
            return connection.introspection.get_constraints(cursor, table)

    # Tests

    def test_creation_deletion(self):
        """
        Tries creating a model's table, and then deleting it.
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Check that it's there
        list(Author.objects.all())
        # Clean up that table
        with connection.schema_editor() as editor:
            editor.delete_model(Author)
        # Check that it's gone
        self.assertRaises(
            DatabaseError,
            lambda: list(Author.objects.all()),
        )

    @unittest.skipUnless(connection.features.supports_foreign_keys, "No FK support")
    def test_fk(self):
        "Tests that creating tables out of FK order, then repointing, works"
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Book)
            editor.create_model(Author)
            editor.create_model(Tag)
        # Check that initial tables are there
        list(Author.objects.all())
        list(Book.objects.all())
        # Make sure the FK constraint is present
        with self.assertRaises(IntegrityError):
            Book.objects.create(
                author_id=1,
                title="Much Ado About Foreign Keys",
                pub_date=datetime.datetime.now(),
            )
        # Repoint the FK constraint
        new_field = ForeignKey(Tag)
        new_field.set_attributes_from_name("author")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Book,
                Book._meta.get_field_by_name("author")[0],
                new_field,
                strict=True,
            )
        # Make sure the new FK constraint is present
        constraints = self.get_constraints(Book._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["author_id"] and details['foreign_key']:
                self.assertEqual(details['foreign_key'], ('schema_tag', 'id'))
                break
        else:
            self.fail("No FK constraint for author_id found")

    @unittest.skipUnless(connection.features.supports_foreign_keys, "No FK support")
    def test_fk_db_constraint(self):
        "Tests that the db_constraint parameter is respected"
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Tag)
            editor.create_model(Author)
            editor.create_model(BookWeak)
        # Check that initial tables are there
        list(Author.objects.all())
        list(Tag.objects.all())
        list(BookWeak.objects.all())
        # Check that BookWeak doesn't have an FK constraint
        constraints = self.get_constraints(BookWeak._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["author_id"] and details['foreign_key']:
                self.fail("FK constraint for author_id found")
        # Make a db_constraint=False FK
        new_field = ForeignKey(Tag, db_constraint=False)
        new_field.set_attributes_from_name("tag")
        with connection.schema_editor() as editor:
            editor.add_field(
                Author,
                new_field,
            )
        # Make sure no FK constraint is present
        constraints = self.get_constraints(Author._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["tag_id"] and details['foreign_key']:
                self.fail("FK constraint for tag_id found")
        # Alter to one with a constraint
        new_field_2 = ForeignKey(Tag)
        new_field_2.set_attributes_from_name("tag")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Author,
                new_field,
                new_field_2,
                strict=True,
            )
        # Make sure the new FK constraint is present
        constraints = self.get_constraints(Author._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["tag_id"] and details['foreign_key']:
                self.assertEqual(details['foreign_key'], ('schema_tag', 'id'))
                break
        else:
            self.fail("No FK constraint for tag_id found")
        # Alter to one without a constraint again
        new_field_2 = ForeignKey(Tag)
        new_field_2.set_attributes_from_name("tag")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Author,
                new_field_2,
                new_field,
                strict=True,
            )
        # Make sure no FK constraint is present
        constraints = self.get_constraints(Author._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["tag_id"] and details['foreign_key']:
                self.fail("FK constraint for tag_id found")

    @unittest.skipUnless(connection.features.supports_foreign_keys, "No FK support")
    def test_m2m_db_constraint(self):
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Tag)
            editor.create_model(Author)
        # Check that initial tables are there
        list(Author.objects.all())
        list(Tag.objects.all())
        # Make a db_constraint=False FK
        new_field = ManyToManyField("schema.Tag", related_name="authors", db_constraint=False)
        new_field.contribute_to_class(Author, "tags")
        # Add the field
        with connection.schema_editor() as editor:
            editor.add_field(
                Author,
                new_field,
            )
        # Make sure no FK constraint is present
        constraints = self.get_constraints(new_field.rel.through._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["tag_id"] and details['foreign_key']:
                self.fail("FK constraint for tag_id found")

    def test_add_field(self):
        """
        Tests adding fields to models
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Ensure there's no age field
        columns = self.column_classes(Author)
        self.assertNotIn("age", columns)
        # Add the new field
        new_field = IntegerField(null=True)
        new_field.set_attributes_from_name("age")
        with connection.schema_editor() as editor:
            editor.add_field(
                Author,
                new_field,
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(Author)
        self.assertEqual(columns['age'][0], "IntegerField")
        self.assertEqual(columns['age'][1][6], True)

    def test_add_field_temp_default(self):
        """
        Tests adding fields to models with a temporary default
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Ensure there's no age field
        columns = self.column_classes(Author)
        self.assertNotIn("age", columns)
        # Add some rows of data
        Author.objects.create(name="Andrew", height=30)
        Author.objects.create(name="Andrea")
        # Add a not-null field
        new_field = CharField(max_length=30, default="Godwin")
        new_field.set_attributes_from_name("surname")
        with connection.schema_editor() as editor:
            editor.add_field(
                Author,
                new_field,
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(Author)
        self.assertEqual(columns['surname'][0], "CharField")
        self.assertEqual(columns['surname'][1][6],
                         connection.features.interprets_empty_strings_as_nulls)

    def test_add_field_temp_default_boolean(self):
        """
        Tests adding fields to models with a temporary default where
        the default is False. (#21783)
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Ensure there's no age field
        columns = self.column_classes(Author)
        self.assertNotIn("age", columns)
        # Add some rows of data
        Author.objects.create(name="Andrew", height=30)
        Author.objects.create(name="Andrea")
        # Add a not-null field
        new_field = BooleanField(default=False)
        new_field.set_attributes_from_name("awesome")
        with connection.schema_editor() as editor:
            editor.add_field(
                Author,
                new_field,
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(Author)
        # BooleanField are stored as TINYINT(1) on MySQL.
        field_type, field_info = columns['awesome']
        if connection.vendor == 'mysql':
            self.assertEqual(field_type, 'IntegerField')
            self.assertEqual(field_info.precision, 1)
        elif connection.vendor == 'oracle' and connection.version_has_default_introspection_bug:
            self.assertEqual(field_type, 'IntegerField')
            self.assertEqual(field_info.precision, 0)
        else:
            self.assertEqual(field_type, 'BooleanField')

    def test_add_field_default_transform(self):
        """
        Tests adding fields to models with a default that is not directly
        valid in the database (#22581)
        """

        class TestTransformField(IntegerField):

            # Weird field that saves the count of items in its value
            def get_default(self):
                return self.default

            def get_prep_value(self, value):
                if value is None:
                    return 0
                return len(value)

        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Add some rows of data
        Author.objects.create(name="Andrew", height=30)
        Author.objects.create(name="Andrea")
        # Add the field with a default it needs to cast (to string in this case)
        new_field = TestTransformField(default={1: 2})
        new_field.set_attributes_from_name("thing")
        with connection.schema_editor() as editor:
            editor.add_field(
                Author,
                new_field,
            )
        # Ensure the field is there
        columns = self.column_classes(Author)
        field_type, field_info = columns['thing']
        self.assertEqual(field_type, 'IntegerField')
        # Make sure the values were transformed correctly
        self.assertEqual(Author.objects.extra(where=["thing = 1"]).count(), 2)

    def test_add_field_binary(self):
        """
        Tests binary fields get a sane default (#22851)
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Add the new field
        new_field = BinaryField(blank=True)
        new_field.set_attributes_from_name("bits")
        with connection.schema_editor() as editor:
            editor.add_field(
                Author,
                new_field,
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(Author)
        # MySQL annoyingly uses the same backend, so it'll come back as one of
        # these two types.
        self.assertIn(columns['bits'][0], ("BinaryField", "TextField"))

    def test_alter(self):
        """
        Tests simple altering of fields
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Ensure the field is right to begin with
        columns = self.column_classes(Author)
        self.assertEqual(columns['name'][0], "CharField")
        self.assertEqual(bool(columns['name'][1][6]), bool(connection.features.interprets_empty_strings_as_nulls))
        # Alter the name field to a TextField
        new_field = TextField(null=True)
        new_field.set_attributes_from_name("name")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Author,
                Author._meta.get_field_by_name("name")[0],
                new_field,
                strict=True,
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(Author)
        self.assertEqual(columns['name'][0], "TextField")
        self.assertEqual(columns['name'][1][6], True)
        # Change nullability again
        new_field2 = TextField(null=False)
        new_field2.set_attributes_from_name("name")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Author,
                new_field,
                new_field2,
                strict=True,
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(Author)
        self.assertEqual(columns['name'][0], "TextField")
        self.assertEqual(bool(columns['name'][1][6]), False)

    def test_alter_null_to_not_null(self):
        """
        #23609 - Tests handling of default values when altering from NULL to NOT NULL.
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Ensure the field is right to begin with
        columns = self.column_classes(Author)
        self.assertTrue(columns['height'][1][6])
        # Create some test data
        Author.objects.create(name='Not null author', height=12)
        Author.objects.create(name='Null author')
        # Verify null value
        self.assertEqual(Author.objects.get(name='Not null author').height, 12)
        self.assertIsNone(Author.objects.get(name='Null author').height)
        # Alter the height field to NOT NULL with default
        new_field = PositiveIntegerField(default=42)
        new_field.set_attributes_from_name("height")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Author,
                Author._meta.get_field_by_name("height")[0],
                new_field
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(Author)
        self.assertFalse(columns['height'][1][6])
        # Verify default value
        self.assertEqual(Author.objects.get(name='Not null author').height, 12)
        self.assertEqual(Author.objects.get(name='Null author').height, 42)

    @unittest.skipUnless(connection.features.supports_combined_alters, "No combined ALTER support")
    def test_alter_null_to_not_null_keeping_default(self):
        """
        #23738 - Can change a nullable field with default to non-nullable
        with the same default.
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(AuthorWithDefaultHeight)
        # Ensure the field is right to begin with
        columns = self.column_classes(AuthorWithDefaultHeight)
        self.assertTrue(columns['height'][1][6])
        # Alter the height field to NOT NULL keeping the previous default
        new_field = PositiveIntegerField(default=42)
        new_field.set_attributes_from_name("height")
        with connection.schema_editor() as editor:
            editor.alter_field(
                AuthorWithDefaultHeight,
                AuthorWithDefaultHeight._meta.get_field_by_name("height")[0],
                new_field,
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(AuthorWithDefaultHeight)
        self.assertFalse(columns['height'][1][6])

    @unittest.skipUnless(connection.features.supports_foreign_keys, "No FK support")
    def test_alter_fk(self):
        """
        Tests altering of FKs
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
            editor.create_model(Book)
        # Ensure the field is right to begin with
        columns = self.column_classes(Book)
        self.assertEqual(columns['author_id'][0], "IntegerField")
        # Make sure the FK constraint is present
        constraints = self.get_constraints(Book._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["author_id"] and details['foreign_key']:
                self.assertEqual(details['foreign_key'], ('schema_author', 'id'))
                break
        else:
            self.fail("No FK constraint for author_id found")
        # Alter the FK
        new_field = ForeignKey(Author, editable=False)
        new_field.set_attributes_from_name("author")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Book,
                Book._meta.get_field_by_name("author")[0],
                new_field,
                strict=True,
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(Book)
        self.assertEqual(columns['author_id'][0], "IntegerField")
        # Make sure the FK constraint is present
        constraints = self.get_constraints(Book._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["author_id"] and details['foreign_key']:
                self.assertEqual(details['foreign_key'], ('schema_author', 'id'))
                break
        else:
            self.fail("No FK constraint for author_id found")

    def test_alter_implicit_id_to_explicit(self):
        """
        Should be able to convert an implicit "id" field to an explicit "id"
        primary key field.
        """
        with connection.schema_editor() as editor:
            editor.create_model(Author)

        new_field = IntegerField(primary_key=True)
        new_field.set_attributes_from_name("id")
        new_field.model = Author
        with connection.schema_editor() as editor:
            editor.alter_field(
                Author,
                Author._meta.get_field_by_name("id")[0],
                new_field,
                strict=True,
            )

    def test_rename(self):
        """
        Tests simple altering of fields
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Ensure the field is right to begin with
        columns = self.column_classes(Author)
        self.assertEqual(columns['name'][0], "CharField")
        self.assertNotIn("display_name", columns)
        # Alter the name field's name
        new_field = CharField(max_length=254)
        new_field.set_attributes_from_name("display_name")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Author,
                Author._meta.get_field_by_name("name")[0],
                new_field,
                strict=True,
            )
        # Ensure the field is right afterwards
        columns = self.column_classes(Author)
        self.assertEqual(columns['display_name'][0], "CharField")
        self.assertNotIn("name", columns)

    def test_m2m_create(self):
        """
        Tests M2M fields on models during creation
        """
        # Create the tables
        with connection.schema_editor() as editor:
            editor.create_model(Author)
            editor.create_model(TagM2MTest)
            editor.create_model(BookWithM2M)
        # Ensure there is now an m2m table there
        columns = self.column_classes(BookWithM2M._meta.get_field_by_name("tags")[0].rel.through)
        self.assertEqual(columns['tagm2mtest_id'][0], "IntegerField")

    def test_m2m_create_through(self):
        """
        Tests M2M fields on models during creation with through models
        """
        # Create the tables
        with connection.schema_editor() as editor:
            editor.create_model(TagThrough)
            editor.create_model(TagM2MTest)
            editor.create_model(BookWithM2MThrough)
        # Ensure there is now an m2m table there
        columns = self.column_classes(TagThrough)
        self.assertEqual(columns['book_id'][0], "IntegerField")
        self.assertEqual(columns['tag_id'][0], "IntegerField")

    def test_m2m(self):
        """
        Tests adding/removing M2M fields on models
        """
        # Create the tables
        with connection.schema_editor() as editor:
            editor.create_model(AuthorWithM2M)
            editor.create_model(TagM2MTest)
        # Create an M2M field
        new_field = ManyToManyField("schema.TagM2MTest", related_name="authors")
        new_field.contribute_to_class(AuthorWithM2M, "tags")
        try:
            # Ensure there's no m2m table there
            self.assertRaises(DatabaseError, self.column_classes, new_field.rel.through)
            # Add the field
            with connection.schema_editor() as editor:
                editor.add_field(
                    Author,
                    new_field,
                )
            # Ensure there is now an m2m table there
            columns = self.column_classes(new_field.rel.through)
            self.assertEqual(columns['tagm2mtest_id'][0], "IntegerField")

            # "Alter" the field. This should not rename the DB table to itself.
            with connection.schema_editor() as editor:
                editor.alter_field(
                    Author,
                    new_field,
                    new_field,
                )

            # Remove the M2M table again
            with connection.schema_editor() as editor:
                editor.remove_field(
                    Author,
                    new_field,
                )
            # Ensure there's no m2m table there
            self.assertRaises(DatabaseError, self.column_classes, new_field.rel.through)
        finally:
            # Cleanup model states
            AuthorWithM2M._meta.local_many_to_many.remove(new_field)

    def test_m2m_through_alter(self):
        """
        Tests altering M2Ms with explicit through models (should no-op)
        """
        # Create the tables
        with connection.schema_editor() as editor:
            editor.create_model(AuthorTag)
            editor.create_model(AuthorWithM2MThrough)
            editor.create_model(TagM2MTest)
        # Ensure the m2m table is there
        self.assertEqual(len(self.column_classes(AuthorTag)), 3)
        # "Alter" the field's blankness. This should not actually do anything.
        with connection.schema_editor() as editor:
            old_field = AuthorWithM2MThrough._meta.get_field_by_name("tags")[0]
            new_field = ManyToManyField("schema.TagM2MTest", related_name="authors", through="AuthorTag")
            new_field.contribute_to_class(AuthorWithM2MThrough, "tags")
            editor.alter_field(
                Author,
                old_field,
                new_field,
            )
        # Ensure the m2m table is still there
        self.assertEqual(len(self.column_classes(AuthorTag)), 3)

    def test_m2m_repoint(self):
        """
        Tests repointing M2M fields
        """
        # Create the tables
        with connection.schema_editor() as editor:
            editor.create_model(Author)
            editor.create_model(BookWithM2M)
            editor.create_model(TagM2MTest)
            editor.create_model(UniqueTest)
        # Ensure the M2M exists and points to TagM2MTest
        constraints = self.get_constraints(BookWithM2M._meta.get_field_by_name("tags")[0].rel.through._meta.db_table)
        if connection.features.supports_foreign_keys:
            for name, details in constraints.items():
                if details['columns'] == ["tagm2mtest_id"] and details['foreign_key']:
                    self.assertEqual(details['foreign_key'], ('schema_tagm2mtest', 'id'))
                    break
            else:
                self.fail("No FK constraint for tagm2mtest_id found")
        # Repoint the M2M
        new_field = ManyToManyField(UniqueTest)
        new_field.contribute_to_class(BookWithM2M, "uniques")
        try:
            with connection.schema_editor() as editor:
                editor.alter_field(
                    Author,
                    BookWithM2M._meta.get_field_by_name("tags")[0],
                    new_field,
                )
            # Ensure old M2M is gone
            self.assertRaises(DatabaseError, self.column_classes, BookWithM2M._meta.get_field_by_name("tags")[0].rel.through)
            # Ensure the new M2M exists and points to UniqueTest
            constraints = self.get_constraints(new_field.rel.through._meta.db_table)
            if connection.features.supports_foreign_keys:
                for name, details in constraints.items():
                    if details['columns'] == ["uniquetest_id"] and details['foreign_key']:
                        self.assertEqual(details['foreign_key'], ('schema_uniquetest', 'id'))
                        break
                else:
                    self.fail("No FK constraint for uniquetest_id found")
        finally:
            # Cleanup through table separately
            with connection.schema_editor() as editor:
                editor.remove_field(BookWithM2M, BookWithM2M._meta.get_field_by_name("uniques")[0])
            # Cleanup model states
            BookWithM2M._meta.local_many_to_many.remove(new_field)
            del BookWithM2M._meta._m2m_cache

    @unittest.skipUnless(connection.features.supports_column_check_constraints, "No check constraints")
    def test_check_constraints(self):
        """
        Tests creating/deleting CHECK constraints
        """
        # Create the tables
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Ensure the constraint exists
        constraints = self.get_constraints(Author._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["height"] and details['check']:
                break
        else:
            self.fail("No check constraint for height found")
        # Alter the column to remove it
        new_field = IntegerField(null=True, blank=True)
        new_field.set_attributes_from_name("height")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Author,
                Author._meta.get_field_by_name("height")[0],
                new_field,
                strict=True,
            )
        constraints = self.get_constraints(Author._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["height"] and details['check']:
                self.fail("Check constraint for height found")
        # Alter the column to re-add it
        with connection.schema_editor() as editor:
            editor.alter_field(
                Author,
                new_field,
                Author._meta.get_field_by_name("height")[0],
                strict=True,
            )
        constraints = self.get_constraints(Author._meta.db_table)
        for name, details in constraints.items():
            if details['columns'] == ["height"] and details['check']:
                break
        else:
            self.fail("No check constraint for height found")

    def test_unique(self):
        """
        Tests removing and adding unique constraints to a single column.
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Tag)
        # Ensure the field is unique to begin with
        Tag.objects.create(title="foo", slug="foo")
        self.assertRaises(IntegrityError, Tag.objects.create, title="bar", slug="foo")
        Tag.objects.all().delete()
        # Alter the slug field to be non-unique
        new_field = SlugField(unique=False)
        new_field.set_attributes_from_name("slug")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Tag,
                Tag._meta.get_field_by_name("slug")[0],
                new_field,
                strict=True,
            )
        # Ensure the field is no longer unique
        Tag.objects.create(title="foo", slug="foo")
        Tag.objects.create(title="bar", slug="foo")
        Tag.objects.all().delete()
        # Alter the slug field to be unique
        new_new_field = SlugField(unique=True)
        new_new_field.set_attributes_from_name("slug")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Tag,
                new_field,
                new_new_field,
                strict=True,
            )
        # Ensure the field is unique again
        Tag.objects.create(title="foo", slug="foo")
        self.assertRaises(IntegrityError, Tag.objects.create, title="bar", slug="foo")
        Tag.objects.all().delete()
        # Rename the field
        new_field = SlugField(unique=False)
        new_field.set_attributes_from_name("slug2")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Tag,
                Tag._meta.get_field_by_name("slug")[0],
                TagUniqueRename._meta.get_field_by_name("slug2")[0],
                strict=True,
            )
        # Ensure the field is still unique
        TagUniqueRename.objects.create(title="foo", slug2="foo")
        self.assertRaises(IntegrityError, TagUniqueRename.objects.create, title="bar", slug2="foo")
        Tag.objects.all().delete()

    def test_unique_together(self):
        """
        Tests removing and adding unique_together constraints on a model.
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(UniqueTest)
        # Ensure the fields are unique to begin with
        UniqueTest.objects.create(year=2012, slug="foo")
        UniqueTest.objects.create(year=2011, slug="foo")
        UniqueTest.objects.create(year=2011, slug="bar")
        self.assertRaises(IntegrityError, UniqueTest.objects.create, year=2012, slug="foo")
        UniqueTest.objects.all().delete()
        # Alter the model to its non-unique-together companion
        with connection.schema_editor() as editor:
            editor.alter_unique_together(
                UniqueTest,
                UniqueTest._meta.unique_together,
                [],
            )
        # Ensure the fields are no longer unique
        UniqueTest.objects.create(year=2012, slug="foo")
        UniqueTest.objects.create(year=2012, slug="foo")
        UniqueTest.objects.all().delete()
        # Alter it back
        new_new_field = SlugField(unique=True)
        new_new_field.set_attributes_from_name("slug")
        with connection.schema_editor() as editor:
            editor.alter_unique_together(
                UniqueTest,
                [],
                UniqueTest._meta.unique_together,
            )
        # Ensure the fields are unique again
        UniqueTest.objects.create(year=2012, slug="foo")
        self.assertRaises(IntegrityError, UniqueTest.objects.create, year=2012, slug="foo")
        UniqueTest.objects.all().delete()

    def test_index_together(self):
        """
        Tests removing and adding index_together constraints on a model.
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Tag)
        # Ensure there's no index on the year/slug columns first
        self.assertEqual(
            False,
            any(
                c["index"]
                for c in self.get_constraints("schema_tag").values()
                if c['columns'] == ["slug", "title"]
            ),
        )
        # Alter the model to add an index
        with connection.schema_editor() as editor:
            editor.alter_index_together(
                Tag,
                [],
                [("slug", "title")],
            )
        # Ensure there is now an index
        self.assertEqual(
            True,
            any(
                c["index"]
                for c in self.get_constraints("schema_tag").values()
                if c['columns'] == ["slug", "title"]
            ),
        )
        # Alter it back
        new_new_field = SlugField(unique=True)
        new_new_field.set_attributes_from_name("slug")
        with connection.schema_editor() as editor:
            editor.alter_index_together(
                Tag,
                [("slug", "title")],
                [],
            )
        # Ensure there's no index
        self.assertEqual(
            False,
            any(
                c["index"]
                for c in self.get_constraints("schema_tag").values()
                if c['columns'] == ["slug", "title"]
            ),
        )

    def test_create_index_together(self):
        """
        Tests creating models with index_together already defined
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(TagIndexed)
        # Ensure there is an index
        self.assertEqual(
            True,
            any(
                c["index"]
                for c in self.get_constraints("schema_tagindexed").values()
                if c['columns'] == ["slug", "title"]
            ),
        )

    def test_db_table(self):
        """
        Tests renaming of the table
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
        # Ensure the table is there to begin with
        columns = self.column_classes(Author)
        self.assertEqual(columns['name'][0], "CharField")
        # Alter the table
        with connection.schema_editor() as editor:
            editor.alter_db_table(
                Author,
                "schema_author",
                "schema_otherauthor",
            )
        # Ensure the table is there afterwards
        Author._meta.db_table = "schema_otherauthor"
        columns = self.column_classes(Author)
        self.assertEqual(columns['name'][0], "CharField")
        # Alter the table again
        with connection.schema_editor() as editor:
            editor.alter_db_table(
                Author,
                "schema_otherauthor",
                "schema_author",
            )
        # Ensure the table is still there
        Author._meta.db_table = "schema_author"
        columns = self.column_classes(Author)
        self.assertEqual(columns['name'][0], "CharField")

    def test_indexes(self):
        """
        Tests creation/altering of indexes
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Author)
            editor.create_model(Book)
        # Ensure the table is there and has the right index
        self.assertIn(
            "title",
            self.get_indexes(Book._meta.db_table),
        )
        # Alter to remove the index
        new_field = CharField(max_length=100, db_index=False)
        new_field.set_attributes_from_name("title")
        with connection.schema_editor() as editor:
            editor.alter_field(
                Book,
                Book._meta.get_field_by_name("title")[0],
                new_field,
                strict=True,
            )
        # Ensure the table is there and has no index
        self.assertNotIn(
            "title",
            self.get_indexes(Book._meta.db_table),
        )
        # Alter to re-add the index
        with connection.schema_editor() as editor:
            editor.alter_field(
                Book,
                new_field,
                Book._meta.get_field_by_name("title")[0],
                strict=True,
            )
        # Ensure the table is there and has the index again
        self.assertIn(
            "title",
            self.get_indexes(Book._meta.db_table),
        )
        # Add a unique column, verify that creates an implicit index
        with connection.schema_editor() as editor:
            editor.add_field(
                Book,
                BookWithSlug._meta.get_field_by_name("slug")[0],
            )
        self.assertIn(
            "slug",
            self.get_indexes(Book._meta.db_table),
        )
        # Remove the unique, check the index goes with it
        new_field2 = CharField(max_length=20, unique=False)
        new_field2.set_attributes_from_name("slug")
        with connection.schema_editor() as editor:
            editor.alter_field(
                BookWithSlug,
                BookWithSlug._meta.get_field_by_name("slug")[0],
                new_field2,
                strict=True,
            )
        self.assertNotIn(
            "slug",
            self.get_indexes(Book._meta.db_table),
        )

    def test_primary_key(self):
        """
        Tests altering of the primary key
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(Tag)
        # Ensure the table is there and has the right PK
        self.assertTrue(
            self.get_indexes(Tag._meta.db_table)['id']['primary_key'],
        )
        # Alter to change the PK
        new_field = SlugField(primary_key=True)
        new_field.set_attributes_from_name("slug")
        new_field.model = Tag
        with connection.schema_editor() as editor:
            editor.remove_field(Tag, Tag._meta.get_field_by_name("id")[0])
            editor.alter_field(
                Tag,
                Tag._meta.get_field_by_name("slug")[0],
                new_field,
            )
        # Ensure the PK changed
        self.assertNotIn(
            'id',
            self.get_indexes(Tag._meta.db_table),
        )
        self.assertTrue(
            self.get_indexes(Tag._meta.db_table)['slug']['primary_key'],
        )

    def test_context_manager_exit(self):
        """
        Ensures transaction is correctly closed when an error occurs
        inside a SchemaEditor context.
        """
        class SomeError(Exception):
            pass
        try:
            with connection.schema_editor():
                raise SomeError
        except SomeError:
            self.assertFalse(connection.in_atomic_block)

    @unittest.skipUnless(connection.features.supports_foreign_keys, "No FK support")
    def test_foreign_key_index_long_names_regression(self):
        """
        Regression test for #21497.
        Only affects databases that supports foreign keys.
        """
        # Create the table
        with connection.schema_editor() as editor:
            editor.create_model(AuthorWithEvenLongerName)
            editor.create_model(BookWithLongName)
        # Find the properly shortened column name
        column_name = connection.ops.quote_name("author_foreign_key_with_really_long_field_name_id")
        column_name = column_name[1:-1].lower()  # unquote, and, for Oracle, un-upcase
        # Ensure the table is there and has an index on the column
        self.assertIn(
            column_name,
            self.get_indexes(BookWithLongName._meta.db_table),
        )

    @unittest.skipUnless(connection.features.supports_foreign_keys, "No FK support")
    def test_add_foreign_key_long_names(self):
        """
        Regression test for #23009.
        Only affects databases that supports foreign keys.
        """
        # Create the initial tables
        with connection.schema_editor() as editor:
            editor.create_model(AuthorWithEvenLongerName)
            editor.create_model(BookWithLongName)
        # Add a second FK, this would fail due to long ref name before the fix
        new_field = ForeignKey(AuthorWithEvenLongerName, related_name="something")
        new_field.set_attributes_from_name("author_other_really_long_named_i_mean_so_long_fk")
        with connection.schema_editor() as editor:
            editor.add_field(
                BookWithLongName,
                new_field,
            )

    def test_creation_deletion_reserved_names(self):
        """
        Tries creating a model's table, and then deleting it when it has a
        SQL reserved name.
        """
        # Create the table
        with connection.schema_editor() as editor:
            try:
                editor.create_model(Thing)
            except OperationalError as e:
                self.fail("Errors when applying initial migration for a model "
                          "with a table named after a SQL reserved word: %s" % e)
        # Check that it's there
        list(Thing.objects.all())
        # Clean up that table
        with connection.schema_editor() as editor:
            editor.delete_model(Thing)
        # Check that it's gone
        self.assertRaises(
            DatabaseError,
            lambda: list(Thing.objects.all()),
        )

    @unittest.skipUnless(connection.features.supports_foreign_keys, "No FK support")
    def test_remove_constraints_capital_letters(self):
        """
        #23065 - Constraint names must be quoted if they contain capital letters.
        """
        def get_field(*args, **kwargs):
            kwargs['db_column'] = "CamelCase"
            field = kwargs.pop('field_class', IntegerField)(*args, **kwargs)
            field.set_attributes_from_name("CamelCase")
            return field

        model = Author
        field = get_field()
        table = model._meta.db_table
        column = field.column

        with connection.schema_editor() as editor:
            editor.create_model(model)
            editor.add_field(model, field)

            editor.execute(
                editor.sql_create_index % {
                    "table": editor.quote_name(table),
                    "name": editor.quote_name("CamelCaseIndex"),
                    "columns": editor.quote_name(column),
                    "extra": "",
                }
            )
            editor.alter_field(model, get_field(db_index=True), field)

            editor.execute(
                editor.sql_create_unique % {
                    "table": editor.quote_name(table),
                    "name": editor.quote_name("CamelCaseUniqConstraint"),
                    "columns": editor.quote_name(field.column),
                }
            )
            editor.alter_field(model, get_field(unique=True), field)

            editor.execute(
                editor.sql_create_fk % {
                    "table": editor.quote_name(table),
                    "name": editor.quote_name("CamelCaseFKConstraint"),
                    "column": editor.quote_name(column),
                    "to_table": editor.quote_name(table),
                    "to_column": editor.quote_name(model._meta.auto_field.column),
                }
            )
            editor.alter_field(model, get_field(Author, field_class=ForeignKey), field)
