from django.shortcuts import render

import pandas as pd
from django.shortcuts import render
from django.core.files.storage import default_storage
import os

def data_mapping_demo(request):
    context = {}
    if request.method == 'POST' and request.FILES.get('dataset'):
        csv_file = request.FILES['dataset']
        # Save uploaded file temporarily
        file_path = default_storage.save('tmp/' + csv_file.name, csv_file)
        abs_file_path = default_storage.path(file_path)
        try:
            df = pd.read_csv(abs_file_path)
            preview = df.head(10).to_html(classes='table table-bordered', index=False)
            # Simulate AI mapping: match columns to example target variables by name similarity
            target_vars = ['PatientID', 'Age', 'Temperature', 'Diagnosis', 'Date']
            mapping = []
            for col in df.columns:
                best_match = max(target_vars, key=lambda t: sum(1 for a, b in zip(col.lower(), t.lower()) if a == b))
                mapping.append((col, best_match))
            context['preview'] = preview
            context['mapping'] = mapping
            context['columns'] = df.columns
        except Exception as e:
            context['error'] = f'Could not process file: {str(e)}'
        finally:
            os.remove(abs_file_path)
    return render(request, 'features/data_mapping.html', context)

