import { describe, test, expect } from 'vitest'
import { en, ru } from '../i18n'

describe('i18n', () => {
  test('EN and RU have identical keys', () => {
    expect(Object.keys(en).sort()).toEqual(Object.keys(ru).sort())
  })
})
