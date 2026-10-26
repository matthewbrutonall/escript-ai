from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0086_seed_default_ontology'),
    ]

    operations = [
        migrations.AddField(
            model_name='ocrmodel',
            name='evaluation_normalization',
            field=models.CharField(
                blank=True,
                choices=[('NFD', 'NFD'), ('NFKD', 'NFKD'), ('NFC', 'NFC'), ('NFKC', 'NFKC')],
                default='',
                help_text="Unicode normalization for held-out ketos evaluation; use the model card's convention.",
                max_length=4,
            ),
        ),
        migrations.AddField(
            model_name='ocrmodel',
            name='evaluation_normalize_whitespace',
            field=models.BooleanField(
                default=True,
                help_text='Normalize whitespace in held-out ketos evaluation when the model card requires it.',
            ),
        ),
    ]
