env "fixture" {
  dev = "docker://postgres/18.6/dev?search_path=public"

  migration {
    dir = "file://tests/fixtures/migrations"
  }
}
