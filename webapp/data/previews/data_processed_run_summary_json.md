```json
{
  "grid": {
    "crs": "EPSG:32735",
    "width": 1673,
    "height": 1597,
    "pixel_m": 10.0
  },
  "valid_fraction": 0.9902,
  "acquisition": {
    "2023-08-12": {
      "nominal": "2023-08-12",
      "source_dates": [
        "2023-07-01",
        "2023-07-04"
      ],
      "source_items": [
        "S2B_MSIL2A_20230701T080609_R078_T35LLG_20230701T134619",
        "S2B_MSIL2A_20230704T081609_R121_T35LLG_20230704T142502"
      ]
    },
    "2023-09-02": {
      "nominal": "2023-09-02",
      "source_dates": [
        "2023-08-25",
        "2023-09-02"
      ],
      "source_items": [
        "S2A_MSIL2A_20230825T080611_R078_T35LLG_20230825T175054",
        "S2B_MSIL2A_20230902T081609_R121_T35LLG_20230902T164949"
      ]
    }
  },
  "boa_offset": {
    "sentinel2_20230812": 1000.0,
    "sentinel2_20230902": 1000.0
  },
  "zones": {
    "source": "provenance",
    "n_zones": 3,
    "zone_source_pairs": [
      {
        "zone": 0,
        "before_source_index": 1,
        "after_source_index": 1
      },
      {
        "zone": 1,
        "before_source_index": 1,
        "after_source_index": 0
      },
      {
        "zone": 2,
        "before_source_index": 0,
        "after_source_index": 0
      }
    ],
    "agreement_with_data_driven_zones": {
      "data_driven_zone_inside_one_source_zone": 0.9934,
      "source_zone_inside_one_data_driven_zone": 0.9105
    }
  },
  "stripes": {
    "angle_deg": 12.6,
    "explained_variance": 0.557,
    "breakpoints_px": [
      310.5,
      814.5,
      886.5
    ],
    "zone_blue_offset": [
      0.0442,
      0.0413,
      0.0254,
      0.0187
    ]
  },
  "registration_shift_px": {
    "0": {
      "shift_px": [
        0.0,
        0.0
      ],
      "magnitude_px": 0.0,
      "max_window_deviation_px": 0.0,
      "n_windows": 8
    },
    "1": {
      "shift_px": [
        0.05,
        0.0
      ],
      "magnitude_px": 0.05,
      "max_window_deviation_px": 0.25,
      "n_windows": 8
    },
    "2": {
      "shift_px": [
        0.0,
        0.0
      ],
      "magnitude_px": 0.0,
      "max_window_deviation_px": 0.1,
      "n_windows": 8
    }
  },
  "calibration": [
    {
      "zone": 0,
      "band": "B02",
      "slope": 1.8383420057569646,
      "intercept": -0.1137789785861969,
      "n_pixels": 1029672,
      "invariant_fraction": 0.886339533365965
    },
    {
      "zone": 0,
      "band": "B03",
      "slope": 1.6293745622467097,
      "intercept": -0.09230593591928482,
      "n_pixels": 1029672,
      "invariant_fraction": 0.886339533365965
    },
    {
      "zone": 0,
      "band": "B04",
      "slope": 1.4983744782619481,
      "intercept": -0.08576266467571259,
      "n_pixels": 1029672,
      "invariant_fraction": 0.886339533365965
    },
    {
      "zone": 1,
      "band": "B02",
      "slope": 1.2571911018206918,
      "intercept": -0.04359988495707512,
      "n_pixels": 134658,
      "invariant_fraction": 0.8477327748815517
    },
    {
      "zone": 1,
      "band": "B03",
      "slope": 1.1945318090116737,
      "intercept": -0.04340938478708267,
      "n_pixels": 134658,
      "invariant_fraction": 0.8477327748815517
    },
    {
      "zone": 1,
      "band": "B04",
      "slope": 1.0776683626509858,
      "intercept": -0.03698120266199112,
      "n_pixels": 134658,
      "invariant_fraction": 0.8477327748815517
    },
    {
      "zone": 2,
      "band": "B02",
      "slope": 1.3240173757058467,
      "intercept": -0.040179017931222916,
      "n_pixels": 1481382,
```
… 242 more lines in the file
