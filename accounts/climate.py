from django.http import JsonResponse
from .climate import suggest_climate


def infer_climate_view(request):
    country = request.GET.get("country")
    region = request.GET.get("region")

    climate = suggest_climate(country, region)

    return JsonResponse({
        "climate": climate
    })
